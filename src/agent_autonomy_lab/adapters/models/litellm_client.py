"""``ModelClient`` over LiteLLM, which fails when a declared setting is not sent.

LiteLLM can change a setting without saying so, and a passing request shows
nothing. At the pinned version, on ``claude-haiku-5-5``:

- ``litellm.drop_params``, which τ-bench sets to ``True`` at import, removes a
  setting the route does not support, even when the call passes ``drop_params=False``.
- ``reasoning_effort`` is sent as ``thinking`` with a token budget, not as an effort.

So the adapter reads the request body LiteLLM built, and every declared setting
must appear in it with its declared value. A setting is therefore declared by
its name in the provider's request (``output_config``, not ``reasoning_effort``).
The check runs on the response, so a call that fails it has already been billed.
"""

from collections.abc import Mapping
from typing import Any

import litellm

from agent_autonomy_lab.domain.events import canonical_json
from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelRequest, ModelResponse

_NOT_SETTINGS = frozenset({"model", "messages", "system"})


def _settings_sent(body: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key not in _NOT_SETTINGS}


def _not_sent(declared: Mapping[str, Any], sent: Mapping[str, Any]) -> list[str]:
    return sorted(
        key
        for key, value in declared.items()
        if key not in sent or canonical_json(sent[key]) != canonical_json(value)
    )


def _cost(response: Any) -> float | None:
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        return None


class LiteLLMModelClient:
    """Calls a model through LiteLLM, which reads the provider's key from the environment."""

    def complete(self, request: ModelRequest) -> ModelResponse:
        bodies: list[Mapping[str, Any]] = []

        def keep_body(details: Mapping[str, Any]) -> None:
            body = (details.get("additional_args") or {}).get("complete_input_dict")
            if isinstance(body, Mapping):
                bodies.append(body)

        messages = [{"role": "system", "content": request.system}] + [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        try:
            response = litellm.completion(
                model=request.model,
                messages=messages,
                drop_params=False,
                logger_fn=keep_body,
                **request.settings,
            )
        except Exception as error:
            raise ModelCallError(f"{type(error).__name__}: {error}") from error

        if not bodies:
            raise ModelCallError("LiteLLM did not show the request it sent")
        sent = _settings_sent(bodies[-1])
        not_sent = _not_sent(request.settings, sent)
        if not_sent:
            raise ModelCallError(f"declared settings not sent as declared: {', '.join(not_sent)}")

        choice = response.choices[0]
        return ModelResponse(
            text=choice.message.content or "",
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_usd=_cost(response),
            finish_reason=choice.finish_reason,
            settings_sent=sent,
        )
