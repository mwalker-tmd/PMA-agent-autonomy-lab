"""The ``ModelClient`` port and its scripted test double."""

from collections.abc import Iterable
from typing import Protocol

from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelRequest, ModelResponse


class ModelClient(Protocol):
    """Sends one request to a model and returns its response.

    It raises ``ModelCallError`` when the call fails, or when a declared setting
    is not in the request sent. A judge turns that into an ``EVALUATOR_ERROR``
    result. The agent and the simulated user do not use this
    port: τ-bench calls their models.
    """

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ScriptedModelClient:
    """Answers each request with the next scripted reply and records what it was asked.

    A reply is the response's text, a full ``ModelResponse``, or an exception to
    raise. Running out of replies raises ``ModelCallError``. A text reply reports the
    request's own model and settings as sent, with no usage and no cost.
    """

    def __init__(self, replies: Iterable[str | ModelResponse | Exception] = ()) -> None:
        self._replies = iter(replies)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        reply = next(self._replies, None)
        if reply is None:
            raise ModelCallError(f"no scripted reply for request {len(self.requests)}")
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ModelResponse):
            return reply
        return ModelResponse(
            text=reply,
            model=request.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            finish_reason="stop",
            settings_sent=dict(request.settings),
        )
