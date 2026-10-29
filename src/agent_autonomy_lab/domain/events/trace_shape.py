"""Checks that a set of trace events forms well-formed traces.

Shared by ``InMemoryTraceSink.link_errors()`` and the trace backend adapters,
so a test double and a real sink accept exactly the same traces.
"""

from collections import Counter
from collections.abc import Iterable, Sequence

from agent_autonomy_lab.domain.events.schema import EventType, TraceEvent


def link_errors(events: Sequence[TraceEvent]) -> list[str]:
    """Describe every broken link; an empty list means the traces are well formed.

    Checks that event IDs and span run IDs are unique; that each simulation
    has exactly one ``conversation`` root; that each child span's parent is a
    recorded conversation span with the same ``RunContext``, which also rules
    out cycles; and that each gate event points at a recorded tool-call span
    for the same tool and with the same ``RunContext``.
    """
    errors = []
    event_ids = Counter(e.event_id for e in events)
    errors += [f"{event_id}: duplicate event_id" for event_id, n in event_ids.items() if n > 1]
    span_ids = Counter(e.run_id for e in events if e.is_span)
    errors += [f"{run_id}: duplicate span run_id" for run_id, n in span_ids.items() if n > 1]

    roots = Counter(
        e.context.simulation_id for e in events if e.event_type is EventType.CONVERSATION
    )
    for simulation_id in dict.fromkeys(e.context.simulation_id for e in events):
        if roots[simulation_id] != 1:
            errors.append(
                f"simulation {simulation_id}: {roots[simulation_id]} conversation roots, expected 1"
            )

    spans = {e.run_id: e for e in events if e.is_span}
    for event in events:
        if event.is_span and event.parent_run_id is not None:
            parent = spans.get(event.parent_run_id)
            if parent is None:
                errors.append(f"{event.event_id}: parent {event.parent_run_id} not recorded")
            elif parent.event_type is not EventType.CONVERSATION:
                errors.append(f"{event.event_id}: parent is not a conversation span")
            elif parent.context != event.context:
                errors.append(f"{event.event_id}: parent has a different run context")
        elif event.event_type is EventType.GATE:
            span = spans.get(event.run_id)
            if span is None or span.event_type is not EventType.TOOL_CALL:
                errors.append(f"{event.event_id}: run {event.run_id} is not a tool-call span")
            elif span.tool_name != event.tool_name:
                errors.append(f"{event.event_id}: tool_name differs from its tool-call span")
            elif span.context != event.context:
                errors.append(f"{event.event_id}: tool-call span has a different run context")
    return errors


def parent_first(events: Iterable[TraceEvent]) -> list[TraceEvent]:
    """Return spans with each conversation root before its children.

    Recording order is kept otherwise, so siblings stay in the order they
    were recorded. Gate events are left out; they are not spans.
    """
    spans = [e for e in events if e.is_span]
    roots = [e for e in spans if e.event_type is EventType.CONVERSATION]
    ordered = []
    for root in roots:
        ordered.append(root)
        ordered += [e for e in spans if e.parent_run_id == root.run_id]
    return ordered
