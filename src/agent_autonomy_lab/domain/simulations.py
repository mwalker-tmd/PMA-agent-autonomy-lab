"""Records of a saved simulation run, independent of the benchmark's file format.

A results reader adapter builds these from a benchmark's saved output
(docs/BASELINE.md, "Result file structure"); the export use case turns them
into trace events and feedback. Only fields the lab uses are kept: provider
payloads such as τ-bench's ``raw_data`` have no place here, so they cannot
reach an export. Timestamps are timezone-aware UTC.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from agent_autonomy_lab.domain.events import Outcome, TokenUsage, json_data


class Role(StrEnum):
    ASSISTANT = "assistant"
    USER = "user"
    TOOL = "tool"


class ExecutionStatus(StrEnum):
    """Whether the environment ran a tool call (τ-bench ``execution_status``)."""

    EXECUTED = "executed"
    REJECTED = "rejected"


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")


@dataclass(frozen=True)
class ToolCall:
    """A tool call the agent proposed: ``call_id`` links it to its result."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("a tool call needs a call_id and a name")
        object.__setattr__(self, "arguments", json_data(self.arguments))


@dataclass(frozen=True)
class Message:
    """One message of a simulation, in conversation order.

    A message produced by a model call has ``tokens`` or ``cost``; the agent's
    fixed opener has neither. A tool result has ``tool_call_id``, ``error``,
    and ``execution_status``.
    """

    role: Role
    turn_index: int
    timestamp: datetime
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    error: bool = False
    execution_status: ExecutionStatus | None = None
    tokens: TokenUsage | None = None
    cost: float | None = None
    generation_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", Role(self.role))
        _require_utc("timestamp", self.timestamp)
        if self.execution_status is not None:
            object.__setattr__(self, "execution_status", ExecutionStatus(self.execution_status))
        if self.role is Role.TOOL and not self.tool_call_id:
            raise ValueError("a tool result needs a tool_call_id")
        if self.tool_calls and self.role is not Role.ASSISTANT:
            raise ValueError("only assistant messages have tool_calls")
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))

    @property
    def tool_outcome(self) -> Outcome:
        """The outcome a tool span records for this result, and evaluators compare it with."""
        if self.execution_status is ExecutionStatus.REJECTED:
            return Outcome.REJECTED
        return Outcome.ERROR if self.error else Outcome.SUCCESS

    @property
    def is_model_output(self) -> bool:
        return self.role is not Role.TOOL and (self.tokens is not None or self.cost is not None)


@dataclass(frozen=True)
class Reward:
    """The benchmark's score for a simulation and how it was reached.

    ``details`` holds the checks behind the score (database check, NL
    assertions, action checks) as strict JSON. They include the task's answer
    key, so they are exported as feedback, never as messages. ``note`` is τ-bench's
    reason when it gave a reward without evaluating, as for a task with no criteria.
    """

    reward: float
    breakdown: Mapping[str, float] = field(default_factory=dict)
    basis: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict, repr=False)
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "breakdown", json_data(self.breakdown))
        object.__setattr__(self, "basis", tuple(self.basis))
        object.__setattr__(self, "details", json_data(self.details))

    @property
    def db_match(self) -> bool | None:
        """Whether the end state equals the reference's, or ``None`` with no DB check."""
        db_check = self.details.get("db_check")
        match = db_check.get("db_match") if isinstance(db_check, Mapping) else None
        return match if isinstance(match, bool) else None


@dataclass(frozen=True)
class SavedSimulation:
    simulation_id: str
    task_id: str
    start_time: datetime
    duration_seconds: float
    termination_reason: str | None
    messages: tuple[Message, ...]
    reward: Reward | None = None

    def __post_init__(self) -> None:
        if not self.simulation_id or not self.task_id:
            raise ValueError("a simulation needs a simulation_id and a task_id")
        _require_utc("start_time", self.start_time)
        object.__setattr__(self, "messages", tuple(self.messages))


@dataclass(frozen=True)
class SavedRun:
    """One saved benchmark run: shared provenance and its simulations."""

    run_name: str
    agent_implementation: str | None
    model_id: str | None
    user_model_id: str | None
    policy_text: str | None
    simulations: tuple[SavedSimulation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "simulations", tuple(self.simulations))
