"""One call to a model, as the core sees it: what is asked, and what came back."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


class ModelCallError(RuntimeError):
    """A model call failed, or did not send what was declared."""


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModelRequest:
    """What to send. ``settings`` holds the declared settings, named as the provider's request.

    The messages must end with a user message. A prompt with only a system
    message fails on Anthropic and is stored with no score (docs/ARCHITECTURE.md,
    "Thread evaluator validation"), and the judge model does not support a reply
    prefilled by a final assistant message, so neither is left to the provider.
    """

    model: str
    system: str
    messages: tuple[ModelMessage, ...]
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages or self.messages[-1].role != "user":
            raise ValueError("a model request must end with a user message")


@dataclass(frozen=True)
class ModelResponse:
    """What came back. ``settings_sent`` holds the effective settings, read from the request sent.

    ``cost_usd`` is ``None`` when the adapter cannot price the model, and the cost
    then comes from the usage and the list price.
    """

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    finish_reason: str | None
    settings_sent: Mapping[str, Any] = field(default_factory=dict)
