"""What model calls used, read two independent ways.

``provider_calls`` reads the recorded HTTP exchanges, which every call passes through, so
a batch's spend cap rests on it. ``CallCounter`` is hook C (ADR-016): a LiteLLM callback,
and the only place τ-bench's own judge calls show their usage and LiteLLM's estimate. It
fails silently (LiteLLM drops a second ``CustomLogger`` of the same class, and an exception
in a callback produces no output), so its count must be compared with the exchanges.
"""

import json
import time
from collections.abc import Iterable, Mapping
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from agent_autonomy_lab.adapters.litellm.cassette import Interaction
from agent_autonomy_lab.domain.batch import ProviderCall

CALLBACK_WAIT_SECONDS = 5.0

# What a request holds besides its settings, on the Responses, Messages, and Chat routes.
_NOT_SETTINGS = frozenset({"model", "input", "instructions", "messages", "system", "tools"})


def _response(interaction: Interaction) -> Mapping[str, Any]:
    try:
        body = json.loads(interaction.response.body)
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, Mapping) else {}


def _request(interaction: Interaction) -> Mapping[str, Any]:
    body = interaction.request.body
    return body if isinstance(body, Mapping) else {}


def _count(usage: Mapping[str, Any], *names: str) -> int | None:
    """The first of ``names`` present: Responses and Messages differ from Chat Completions."""
    return next((usage[name] for name in names if isinstance(usage.get(name), int)), None)


def provider_calls(interactions: Iterable[Interaction]) -> tuple[ProviderCall, ...]:
    """Each exchange's model, status, and the usage its response reports."""
    calls = []
    for interaction in interactions:
        usage = _response(interaction).get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        calls.append(
            ProviderCall(
                model=str(_request(interaction).get("model", "unknown")),
                status=interaction.response.status,
                input_tokens=_count(usage, "input_tokens", "prompt_tokens"),
                output_tokens=_count(usage, "output_tokens", "completion_tokens"),
            )
        )
    return tuple(calls)


def _role(request: Mapping[str, Any], judge_model: str | None) -> str:
    if request.get("model") == judge_model:
        return "benchmark_judge"
    return "agent" if request.get("tools") else "user"


def effective_settings(
    interactions: Iterable[Interaction], benchmark_judge_model: str | None = None
) -> dict[str, dict[str, Any]]:
    """Per role, the settings its first request carried and the effort the model reported.

    Only the agent's requests carry tools. ADR-020 records why a declared setting is not
    taken as what ran: ``reasoning_effort`` does not reach the model on the Responses bridge.
    """
    seen: dict[str, dict[str, Any]] = {}
    for interaction in interactions:
        request = _request(interaction)
        role = _role(request, benchmark_judge_model)
        if role in seen or not request:
            continue
        reasoning = _response(interaction).get("reasoning")
        seen[role] = {
            "sent": {k: v for k, v in sorted(request.items()) if k not in _NOT_SETTINGS},
            "reported_effort": reasoning.get("effort") if isinstance(reasoning, Mapping) else None,
        }
    return seen


class CallCounter(CustomLogger):
    """Hook C: what a LiteLLM success callback receives for each model call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.own_ms = 0.0

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        began = time.perf_counter()
        usage = getattr(response_obj, "usage", None)
        self.calls.append(
            {
                "model": kwargs.get("model"),
                "cost": kwargs.get("response_cost"),
                "prompt": getattr(usage, "prompt_tokens", 0),
                "completion": getattr(usage, "completion_tokens", 0),
            }
        )
        self.own_ms += (time.perf_counter() - began) * 1000

    def wait_for(self, count: int) -> None:
        """LiteLLM calls back on a background thread; give it time to reach ``count``."""
        deadline = time.monotonic() + CALLBACK_WAIT_SECONDS
        while len(self.calls) < count and time.monotonic() < deadline:
            time.sleep(0.01)
