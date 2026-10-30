"""Records of a live agent run, captured turn by turn by an agent adapter.

A saved run (``domain.simulations``) is what the benchmark wrote after the
fact. A live recording is what the lab's agent wrapper saw while the run was
under way: every message that reached the agent, every reply, the lab's own
timing of each agent call, and a fingerprint of what the agent sent to its
model besides the conversation. Timestamps are timezone-aware UTC.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from agent_autonomy_lab.domain.events import json_data
from agent_autonomy_lab.domain.simulations import Message


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")


@dataclass(frozen=True)
class AgentFingerprint:
    """What an agent sends its model on every call, apart from the conversation.

    Two agents with equal fingerprints send the same system prompt, tool
    schemas, and model arguments to the same model. The system prompt embeds
    the policy text the agent received, so ``prompt_version`` covers it. ``model_args`` is kept in
    full (it holds no secrets, only settings such as ``reasoning_effort`` and
    ``seed``) so a trace can show it.
    """

    model_id: str
    prompt_version: str
    tool_schema_version: str
    model_args_version: str
    model_args: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_args", json_data(self.model_args))

    def differences(self, other: "AgentFingerprint") -> list[str]:
        """Names of the fields whose values differ from ``other``'s."""
        names = ("model_id", "prompt_version", "tool_schema_version", "model_args_version")
        return [name for name in names if getattr(self, name) != getattr(other, name)]


@dataclass(frozen=True)
class AgentTurn:
    """One call to the agent: the messages it received and the reply it produced.

    ``inputs`` are the customer message or the tool results the turn answers.
    ``started_at`` and ``elapsed_ms`` time the delegated agent call, which
    makes one model call. When the call raised, ``output`` is ``None`` and
    ``error_class`` and ``error_message`` describe the exception.
    ``turn_index`` is the reply's position in the transcript, or the position
    it would have had.
    """

    turn_index: int
    started_at: datetime
    elapsed_ms: float
    fingerprint: AgentFingerprint
    inputs: tuple[Message, ...]
    output: Message | None = None
    error_class: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_utc("started_at", self.started_at)
        object.__setattr__(self, "inputs", tuple(self.inputs))
        if (self.output is None) == (self.error_class is None):
            raise ValueError("a turn has either an output or an error_class")


@dataclass(frozen=True)
class LiveRecording:
    """One simulation as the agent wrapper recorded it.

    ``transcript`` is every message the agent saw or sent, in order, with
    ``turn_index`` set to its position. ``overhead_ms`` is the time the
    wrapper spent recording, outside the agent calls it timed.
    """

    agent_version: str
    started_at: datetime
    ended_at: datetime
    transcript: tuple[Message, ...]
    turns: tuple[AgentTurn, ...]
    overhead_ms: float

    def __post_init__(self) -> None:
        _require_utc("started_at", self.started_at)
        _require_utc("ended_at", self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError("ended_at is before started_at")
        object.__setattr__(self, "transcript", tuple(self.transcript))
        object.__setattr__(self, "turns", tuple(self.turns))

    @property
    def elapsed_ms(self) -> float:
        return (self.ended_at - self.started_at) / timedelta(milliseconds=1)

    @property
    def fingerprint(self) -> AgentFingerprint | None:
        """The fingerprint every turn shares, or ``None`` if there are no turns or they differ."""
        prints = {turn.fingerprint for turn in self.turns}
        return prints.pop() if len(prints) == 1 else None


INJECTED_FAULT = "injected_fault"


@dataclass(frozen=True)
class InjectedFault:
    """A tool call the lab failed on purpose (``adapters/tau2/fault_injection.py``).

    τ-bench records only ``error: true`` on a tool result, so this record is
    what classifies the failure: the trace gives the tool span answering
    ``call_id`` this ``error_class``.
    """

    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    error_class: str
    error_message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", json_data(self.arguments))


UNFINISHED_PREFIX = "unfinished-"


@dataclass(frozen=True)
class UnfinishedSimulation:
    """A live run that raised before the benchmark returned a simulation.

    τ-bench assigns its simulation ID inside the runner and does not expose
    it when the run raises, so the lab assigns ``simulation_id`` itself. It
    starts with ``unfinished-`` so it is never mistaken for a benchmark ID.
    An unfinished run has no reward and no termination reason; ``error_class``
    and ``error_message`` describe the exception that ended it.
    """

    simulation_id: str
    task_id: str
    error_class: str
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not self.simulation_id.startswith(UNFINISHED_PREFIX):
            raise ValueError(f"an unfinished run's ID starts with {UNFINISHED_PREFIX!r}")
        if not self.task_id or not self.error_class:
            raise ValueError("an unfinished run needs a task_id and an error_class")

    @property
    def reward(self) -> None:
        return None

    @property
    def termination_reason(self) -> None:
        return None
