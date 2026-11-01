"""Read a simulation's gate events, and say whether a gate was active at all.

``autonomy_level: "n/a"`` means no gate decided anything. It is never compliance, and
it is not level 0, so an ungated run gets its own label on every count. A trace that
cannot say which it is has no gate state to read (docs/EVALUATION.md, "Unauthorized
attempts"). Nothing emits gate events until Milestone 6, so only fixtures reach the
gated path for now.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    GateDecision,
    GateEventClass,
    TraceEvent,
    link_errors,
)

OUTSIDE_AUTONOMY_LEVEL = "outside_autonomy_level"
"""``decision_reason`` of a denial because the run's level does not grant the write (ADR-023)."""

_DECISIONS = {
    GateEventClass.DENIED: GateDecision.DENIED,
    GateEventClass.PENDING_APPROVAL: GateDecision.PENDING_APPROVAL,
    GateEventClass.APPROVED: GateDecision.ALLOWED,
}


class GateMode(StrEnum):
    """What a count is labeled with, so counts from different modes are never merged."""

    UNGATED = "no gate active"
    GATED = "gate active"
    UNKNOWN = "gate state unknown"


@dataclass(frozen=True)
class GateLog:
    """The gate mode of one simulation, and its gate events per tool span ``run_id``."""

    mode: GateMode
    problem: str | None = None
    runs: Mapping[UUID, tuple[TraceEvent, ...]] = field(default_factory=dict)


def _problem(events: Sequence[TraceEvent], gate_events: Sequence[TraceEvent]) -> str | None:
    """Say why the trace cannot establish its gate state, if it cannot."""
    if not events:
        return "The trace has no events, so whether a gate was active is unknown."
    levels = {event.context.autonomy_level for event in events}
    if len(levels) > 1:
        return "The trace's events disagree on the autonomy level."
    if levels == {NO_GATE} and gate_events:
        return 'The trace has gate events, but its autonomy level is "n/a".'
    if levels == {NO_GATE}:
        return None
    errors = link_errors(events)
    if errors:
        return f"The trace's gate events cannot be bound to its spans: {errors[0]}."
    for event in gate_events:
        expected = _DECISIONS.get(event.gate_event)
        if expected and event.decision and event.decision is not expected:
            return f"A {event.gate_event} gate event carries the decision {event.decision}."
    return None


def read_gate_log(events: Iterable[TraceEvent]) -> GateLog:
    """Group the gate events by the call they annotate, in time order.

    Events with one timestamp keep their recording order.
    """
    events = tuple(events)
    gate_events = tuple(event for event in events if event.event_type is EventType.GATE)
    problem = _problem(events, gate_events)
    if problem is not None:
        return GateLog(GateMode.UNKNOWN, problem)
    if events[0].context.autonomy_level == NO_GATE:
        return GateLog(GateMode.UNGATED)
    runs: dict[UUID, list[TraceEvent]] = {}
    for event in sorted(gate_events, key=lambda event: event.timestamp):
        runs.setdefault(event.run_id, []).append(event)
    return GateLog(GateMode.GATED, runs={run: tuple(group) for run, group in runs.items()})


def has_event(events: Iterable[TraceEvent], *classes: GateEventClass) -> bool:
    return any(event.gate_event in classes for event in events)


def approval_stands(
    events: Sequence[TraceEvent], call_hash: str | None, ran_at: datetime | None = None
) -> bool | None:
    """Whether an ``APPROVED`` for exactly these arguments still held when the tool ran.

    The tool ran at its execution event or, with none, at ``ran_at``, the time of its
    result. Nothing recorded after that can have authorized it or taken that back.

    A later ``DENIED`` or an approval of other arguments ends it, since changed arguments
    invalidate an approval (docs/AUTONOMY.md, "Required invariants"). With a hash missing
    on either side the match is unknown, so this returns ``None``.
    """
    if call_hash is None:
        return None
    stands = False
    for event in events:
        if event.gate_event in (GateEventClass.EXECUTED, GateEventClass.EXECUTION_FAILED):
            break
        if ran_at is not None and event.timestamp > ran_at:
            break
        if event.gate_event is GateEventClass.DENIED:
            stands = False
        elif event.gate_event is GateEventClass.APPROVED:
            if event.argument_hash is None:
                return None
            stands = event.argument_hash == call_hash
    return stands
