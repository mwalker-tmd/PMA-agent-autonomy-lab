"""Record every model call LiteLLM makes, and replay them without a provider.

Reasoning models do not repeat a conversation from a seed, so behavioral
equivalence is shown on a **deterministic replay**: one live run is recorded
as a cassette, and later runs are served from it. The seam is LiteLLM's
HTTP layer: ``sync_transport`` is used by every synchronous HTTP client
LiteLLM builds itself (the OpenAI Responses bridge, Anthropic), and
``client_session`` by the OpenAI SDK client behind chat completions. Between
them they see each provider request exactly as sent (the agent, the
simulated user, and the benchmark's judges alike) without any change to
τ-bench or to the request. Async calls are not covered; τ-bench calls
LiteLLM synchronously.

A LiteLLM custom provider was tried first and rejected: LiteLLM drops
parameters a custom provider does not declare, such as ``reasoning_effort``,
so it would not see the request the baseline sends.

The cassette keeps each request's method, URL, and body, and each
response's status, content type, and body. Request headers (which carry the
API key) and other response headers are never stored.

``ReplayTransport`` serves the recorded responses in order and fails on the
first request that differs from the recording or comes after its end. Once
it has failed, it fails every later request too, so a retry cannot move
past the mismatch; the caller checks ``check_complete()`` after the run,
because τ-bench's evaluator catches some judge errors.
"""

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import litellm

CASSETTE_FORMAT = "lab-cassette/1"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    body: Any  # parsed JSON, or the text when the body is not JSON


@dataclass(frozen=True)
class RecordedResponse:
    status: int
    content_type: str | None
    body: str


@dataclass(frozen=True)
class Interaction:
    request: RecordedRequest
    response: RecordedResponse


@dataclass(frozen=True)
class Cassette:
    """A recorded run's provider traffic, and the settings it was recorded with."""

    interactions: tuple[Interaction, ...]
    settings: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        data = {
            "format": CASSETTE_FORMAT,
            "settings": dict(self.settings),
            "interactions": [
                {
                    "request": vars(i.request),
                    "response": vars(i.response),
                }
                for i in self.interactions
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Cassette":
        data = json.loads(text)
        if data.get("format") != CASSETTE_FORMAT:
            raise ValueError(f"not a {CASSETTE_FORMAT} cassette: {data.get('format')!r}")
        return cls(
            interactions=tuple(
                Interaction(
                    RecordedRequest(**item["request"]), RecordedResponse(**item["response"])
                )
                for item in data["interactions"]
            ),
            settings=data.get("settings") or {},
        )

    def save(self, path: Path) -> str:
        """Write the cassette, refusing to overwrite one; return its file's SHA-256."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as file:
            file.write(self.to_json())
        return file_sha256(path)

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        return cls.from_json(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(request: httpx.Request) -> RecordedRequest:
    text = request.content.decode("utf-8")
    try:
        body: Any = json.loads(text)
    except json.JSONDecodeError:
        body = text
    return RecordedRequest(method=request.method, url=str(request.url), body=body)


class RecordingTransport(httpx.BaseTransport):
    """Forwards each request to ``inner`` and records the exchange."""

    def __init__(self, inner: httpx.BaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.HTTPTransport()
        self.interactions: list[Interaction] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        recorded = _request(request)
        response = self._inner.handle_request(request)
        body = response.read()
        self.interactions.append(
            Interaction(
                recorded,
                RecordedResponse(
                    status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    body=body.decode("utf-8"),
                ),
            )
        )
        return response

    def cassette(self, settings: Mapping[str, Any]) -> Cassette:
        return Cassette(tuple(self.interactions), settings)

    def close(self) -> None:
        self._inner.close()


class CassetteMismatch(RuntimeError):
    """A replayed run sent a request the recording does not have at that position."""


class ReplayTransport(httpx.BaseTransport):
    """Serves a cassette's responses in order; no request reaches a provider."""

    def __init__(self, cassette: Cassette) -> None:
        self._interactions = cassette.interactions
        self.served = 0
        self.failure: CassetteMismatch | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.failure is not None:
            raise self.failure
        actual = _request(request)
        if self.served >= len(self._interactions):
            self.failure = CassetteMismatch(
                f"request {self.served + 1} has no recording: the cassette holds "
                f"{len(self._interactions)} ({actual.method} {actual.url})"
            )
            raise self.failure
        expected = self._interactions[self.served]
        if actual != expected.request:
            self.failure = CassetteMismatch(
                f"request {self.served + 1} differs from the recording in "
                f"{', '.join(_differing_fields(expected.request, actual))}"
            )
            raise self.failure
        self.served += 1
        recorded = expected.response
        headers = {"content-type": recorded.content_type} if recorded.content_type else {}
        return httpx.Response(
            recorded.status, headers=headers, content=recorded.body.encode("utf-8"), request=request
        )

    @property
    def remaining(self) -> int:
        return len(self._interactions) - self.served

    def check_complete(self) -> None:
        """Raise unless every request matched and every recorded response was served."""
        if self.failure is not None:
            raise self.failure
        if self.remaining:
            raise CassetteMismatch(
                f"the run ended with {self.remaining} of {len(self._interactions)} "
                "recorded responses unserved"
            )


def _differing_fields(expected: RecordedRequest, actual: RecordedRequest) -> list[str]:
    names = [name for name in ("method", "url") if getattr(expected, name) != getattr(actual, name)]
    if expected.body != actual.body:
        if isinstance(expected.body, dict) and isinstance(actual.body, dict):
            keys = sorted(set(expected.body) | set(actual.body))
            names += [f"body.{k}" for k in keys if expected.body.get(k) != actual.body.get(k)]
        else:
            names.append("body")
    return names


def _flush_clients() -> None:
    cache = getattr(litellm, "in_memory_llm_clients_cache", None)
    if cache is not None:
        cache.flush_cache()


@contextmanager
def litellm_transport(transport: httpx.BaseTransport) -> Iterator[httpx.BaseTransport]:
    """Route every synchronous LiteLLM HTTP request through ``transport``.

    Sets both ``litellm.sync_transport`` and ``litellm.client_session`` (which
    τ-bench sets at import) and restores them afterwards. LiteLLM caches its
    HTTP clients, so the cache is flushed on entry and on exit: a client
    built before entry would bypass the transport, and one built inside
    would keep it afterwards.
    """
    previous = getattr(litellm, "sync_transport", None), litellm.client_session
    _flush_clients()
    litellm.sync_transport = transport
    litellm.client_session = httpx.Client(transport=transport)
    try:
        yield transport
    finally:
        litellm.sync_transport, litellm.client_session = previous
        _flush_clients()


REPLAY_API_KEY = "sk-replay-offline-no-provider"
_PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@contextmanager
def replaying(cassette: Cassette) -> Iterator[ReplayTransport]:
    """Serve every LiteLLM request from ``cassette`` while the block runs.

    Provider API keys are replaced by a placeholder for the duration, so a
    request that somehow bypassed the transport would be refused by the
    provider instead of being billed.
    """
    saved = {name: os.environ.get(name) for name in _PROVIDER_KEYS}
    for name in _PROVIDER_KEYS:
        os.environ[name] = REPLAY_API_KEY
    try:
        transport = ReplayTransport(cassette)
        with litellm_transport(transport):
            yield transport
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
