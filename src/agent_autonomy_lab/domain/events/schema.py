"""Versioned trace event schema (docs/ARCHITECTURE.md, "Event and identifier contract").

A ``TraceEvent`` is one node of a simulation's trace. Conversation, model-call,
and tool-call events are spans: each has its own ``run_id`` and, except the
conversation root, a ``parent_run_id``. Gate events are not spans; they
annotate the tool-call span named by their ``run_id``, so one call's
``ATTEMPTED`` → ``APPROVED`` → ``EXECUTED`` transitions stay separate events.

Fields shared by every event of a simulation live in ``RunContext``.
"""

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

SCHEMA_VERSION = "trace-event/1"

NO_GATE: Literal["n/a"] = "n/a"
"""``autonomy_level`` when no authorization gate is active (Milestones 3–5).

Distinct from level 0: level 0 is a gate that denies every write, while
``"n/a"`` means no gate decided anything.
"""

AUTONOMY_LEVELS = (0, 1, 2, 3)

AutonomyLevel = int | Literal["n/a"]


class EventType(StrEnum):
    CONVERSATION = "conversation"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    GATE = "gate"


SPAN_TYPES = frozenset({EventType.CONVERSATION, EventType.MODEL_CALL, EventType.TOOL_CALL})


class GateEventClass(StrEnum):
    """Gate state transitions from docs/AUTONOMY.md. Emitted from Milestone 6."""

    ATTEMPTED = "ATTEMPTED"
    DENIED = "DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class GateDecision(StrEnum):
    """What the ``AuthorizationGate`` port returns for a proposed tool call."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"


class Outcome(StrEnum):
    """How a span ended.

    For tool calls: τ-bench ``execution_status: executed`` with ``error: false``
    is ``SUCCESS``, with ``error: true`` is ``ERROR``, and
    ``execution_status: rejected`` is ``REJECTED``.
    """

    SUCCESS = "success"
    ERROR = "error"
    REJECTED = "rejected"


class TrafficType(StrEnum):
    SYNTHETIC = "synthetic"


class ExperimentClass(StrEnum):
    OFFICIAL_UPSTREAM_BASELINE = "official_upstream_baseline"
    CUSTOM_LAB_EXPERIMENT = "custom_lab_experiment"


def _snapshot(name: str, content: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a detached, JSON-only copy of span content, or raise ``ValueError``."""
    if content is None:
        return None
    try:
        return json.loads(json.dumps(dict(content), allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be JSON-serializable without NaN or infinity") from error


def _require_str(obj: object, names: tuple[str, ...], *, required: bool) -> None:
    for name in names:
        value = getattr(obj, name)
        if value is None and not required:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")


def _require_type(name: str, value: object, kind: type, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, kind):
        raise ValueError(f"{name} must be a {kind.__name__}")


def _coerce_enum(obj: object, name: str, enum: type[StrEnum], *, optional: bool = False) -> None:
    """Accept an enum member or its value; store the member so ``is`` checks hold."""
    value = getattr(obj, name)
    if value is None and optional:
        return
    try:
        object.__setattr__(obj, name, enum(value))
    except ValueError as error:
        raise ValueError(f"{name} must be one of {[m.value for m in enum]}") from error


def _require_number(name: str, value: object, *, integer: bool) -> None:
    """Require a finite, non-negative number; booleans are rejected."""
    kinds = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds):
        raise ValueError(f"{name} must be {'an integer' if integer else 'a number'}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _require_utc(name: str, value: datetime) -> None:
    _require_type(name, value, datetime)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC; normalize it with to_utc()")


@dataclass(frozen=True)
class RunContext:
    """Provenance shared by every event of one simulation."""

    simulation_id: str
    thread_id: str
    task_id: str
    autonomy_level: AutonomyLevel
    experiment_class: ExperimentClass
    traffic_type: TrafficType = TrafficType.SYNTHETIC
    fault_injected: bool = False
    scenario_id: str | None = None
    workflow: str | None = None
    scenario_category: str | None = None
    agent_version: str | None = None
    prompt_version: str | None = None
    model_id: str | None = None
    user_model_id: str | None = None
    tool_schema_version: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        _require_str(self, ("simulation_id", "thread_id", "task_id"), required=True)
        _require_str(
            self,
            (
                "scenario_id", "workflow", "scenario_category", "agent_version", "prompt_version",
                "model_id", "user_model_id", "tool_schema_version", "policy_version",
            ),
            required=False,
        )  # fmt: skip
        _coerce_enum(self, "experiment_class", ExperimentClass)
        _coerce_enum(self, "traffic_type", TrafficType)
        _require_type("fault_injected", self.fault_injected, bool)
        level = self.autonomy_level
        if level != NO_GATE and (
            isinstance(level, bool) or not isinstance(level, int) or level not in AUTONOMY_LEVELS
        ):
            raise ValueError(f"autonomy_level must be one of {AUTONOMY_LEVELS} or {NO_GATE!r}")


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int

    def __post_init__(self) -> None:
        for name in ("prompt_tokens", "completion_tokens"):
            _require_number(name, getattr(self, name), integer=True)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TraceEvent:
    """One versioned trace event.

    ``inputs`` and ``outputs`` carry span content (messages, tool arguments and
    results) for the trace backend. They are copied at construction, so later
    changes to the caller's objects do not alter a recorded event. They are not
    part of the identifier contract, and adapters redact them before export.
    """

    event_id: UUID
    event_type: EventType
    timestamp: datetime
    context: RunContext
    run_id: UUID
    parent_run_id: UUID | None = None
    turn_index: int | None = None
    tool_name: str | None = None
    argument_hash: str | None = None
    gate_event: GateEventClass | None = None
    decision: GateDecision | None = None
    decision_reason: str | None = None
    outcome: Outcome | None = None
    error_class: str | None = None
    error_message: str | None = None
    elapsed_ms: float | None = None
    tokens: TokenUsage | None = None
    cost: float | None = None
    inputs: Mapping[str, Any] | None = field(default=None, repr=False)
    outputs: Mapping[str, Any] | None = field(default=None, repr=False)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_utc("timestamp", self.timestamp)
        for name in ("event_id", "run_id"):
            _require_type(name, getattr(self, name), UUID)
        _require_type("parent_run_id", self.parent_run_id, UUID, optional=True)
        _require_type("context", self.context, RunContext)
        _require_type("tokens", self.tokens, TokenUsage, optional=True)
        _coerce_enum(self, "event_type", EventType)
        _coerce_enum(self, "gate_event", GateEventClass, optional=True)
        _coerce_enum(self, "decision", GateDecision, optional=True)
        _coerce_enum(self, "outcome", Outcome, optional=True)
        _require_str(
            self,
            ("tool_name", "argument_hash", "decision_reason", "error_class", "error_message"),
            required=False,
        )
        if self.turn_index is not None:
            _require_number("turn_index", self.turn_index, integer=True)
        for name in ("elapsed_ms", "cost"):
            if getattr(self, name) is not None:
                _require_number(name, getattr(self, name), integer=False)
        object.__setattr__(self, "inputs", _snapshot("inputs", self.inputs))
        object.__setattr__(self, "outputs", _snapshot("outputs", self.outputs))
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version!r}")
        self._check_links()
        self._check_gate_fields()
        self._check_tool_fields()
        self._check_outcome_fields()

    def _check_links(self) -> None:
        if self.event_type is EventType.CONVERSATION and self.parent_run_id is not None:
            raise ValueError("a conversation event is a root span and has no parent_run_id")
        if self.event_type in (EventType.MODEL_CALL, EventType.TOOL_CALL):
            if self.parent_run_id is None:
                raise ValueError(f"a {self.event_type} event needs a parent_run_id")
        if self.event_type is EventType.GATE and self.parent_run_id is not None:
            raise ValueError("a gate event has no parent_run_id; its run_id is the tool-call span")
        if self.parent_run_id is not None and self.parent_run_id == self.run_id:
            raise ValueError("parent_run_id cannot equal run_id")

    def _check_gate_fields(self) -> None:
        if self.event_type is EventType.GATE:
            if self.gate_event is None:
                raise ValueError("a gate event needs a gate_event class")
        elif any(v is not None for v in (self.gate_event, self.decision, self.decision_reason)):
            raise ValueError("gate_event, decision, and decision_reason belong on gate events only")

    def _check_tool_fields(self) -> None:
        if self.event_type in (EventType.TOOL_CALL, EventType.GATE) and not self.tool_name:
            raise ValueError(f"a {self.event_type} event needs a tool_name")
        if self.argument_hash is not None and not self.tool_name:
            raise ValueError("argument_hash needs a tool_name")

    def _check_outcome_fields(self) -> None:
        if self.outcome is not None and self.event_type not in SPAN_TYPES:
            raise ValueError("outcome belongs on span events only")
        if (self.error_class or self.error_message) and self.outcome is not Outcome.ERROR:
            raise ValueError("error_class and error_message need outcome=error")

    @property
    def is_span(self) -> bool:
        return self.event_type in SPAN_TYPES

    def to_dict(self) -> dict[str, Any]:
        """Return a flat, JSON-ready record with the context fields inlined."""
        ctx = self.context
        return {
            "schema_version": self.schema_version,
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "simulation_id": ctx.simulation_id,
            "thread_id": ctx.thread_id,
            "task_id": ctx.task_id,
            "scenario_id": ctx.scenario_id,
            "run_id": str(self.run_id),
            "parent_run_id": str(self.parent_run_id) if self.parent_run_id else None,
            "turn_index": self.turn_index,
            "agent_version": ctx.agent_version,
            "prompt_version": ctx.prompt_version,
            "model_id": ctx.model_id,
            "user_model_id": ctx.user_model_id,
            "tool_schema_version": ctx.tool_schema_version,
            "policy_version": ctx.policy_version,
            "autonomy_level": ctx.autonomy_level,
            "traffic_type": ctx.traffic_type.value,
            "experiment_class": ctx.experiment_class.value,
            "fault_injected": ctx.fault_injected,
            "workflow": ctx.workflow,
            "scenario_category": ctx.scenario_category,
            "tool_name": self.tool_name,
            "argument_hash": self.argument_hash,
            "gate_event": self.gate_event.value if self.gate_event else None,
            "decision": self.decision.value if self.decision else None,
            "decision_reason": self.decision_reason,
            "outcome": self.outcome.value if self.outcome else None,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "elapsed_ms": self.elapsed_ms,
            "prompt_tokens": self.tokens.prompt_tokens if self.tokens else None,
            "completion_tokens": self.tokens.completion_tokens if self.tokens else None,
            "cost": self.cost,
            "inputs": copy.deepcopy(self.inputs),
            "outputs": copy.deepcopy(self.outputs),
        }
