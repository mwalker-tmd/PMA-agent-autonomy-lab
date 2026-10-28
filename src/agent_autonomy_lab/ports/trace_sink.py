"""The ``TraceSink`` port and its in-memory test double."""

from typing import Protocol
from uuid import UUID

from agent_autonomy_lab.domain.events import EventType, TraceEvent


class TraceSink(Protocol):
    """Records trace events for a trace backend.

    Spans are recorded when they finish, so a child is usually recorded before
    its parent; a sink must not require parents first. ``flush`` blocks until
    every recorded event has been delivered.
    """

    def record(self, event: TraceEvent) -> None: ...

    def flush(self) -> None: ...


class InMemoryTraceSink:
    """Keeps events in a list and checks the identifier contract on demand."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.flush_count = 0
        self._event_ids: set[UUID] = set()
        self._spans: dict[UUID, TraceEvent] = {}

    def record(self, event: TraceEvent) -> None:
        if event.event_id in self._event_ids:
            raise ValueError(f"duplicate event_id {event.event_id}")
        if event.is_span and event.run_id in self._spans:
            raise ValueError(f"duplicate span run_id {event.run_id}")
        self._event_ids.add(event.event_id)
        if event.is_span:
            self._spans[event.run_id] = event
        self.events.append(event)

    def flush(self) -> None:
        self.flush_count += 1

    def span(self, run_id: UUID) -> TraceEvent:
        return self._spans[run_id]

    def children(self, run_id: UUID) -> list[TraceEvent]:
        """Child spans of ``run_id``, in recording order."""
        return [e for e in self.events if e.is_span and e.parent_run_id == run_id]

    def gate_events(self, run_id: UUID) -> list[TraceEvent]:
        """Gate events that annotate the tool-call span ``run_id``, in recording order."""
        return [e for e in self.events if e.event_type is EventType.GATE and e.run_id == run_id]

    def roots(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type is EventType.CONVERSATION]

    def link_errors(self) -> list[str]:
        """Describe every broken link; an empty list means the trace is well formed.

        Checks that each child span's parent is a recorded conversation span
        in the same simulation, which also rules out cycles, and that each gate
        event points at a recorded tool-call span for the same tool.
        """
        errors = []
        for event in self.events:
            if event.is_span and event.parent_run_id is not None:
                parent = self._spans.get(event.parent_run_id)
                if parent is None:
                    errors.append(f"{event.event_id}: parent {event.parent_run_id} not recorded")
                elif parent.event_type is not EventType.CONVERSATION:
                    errors.append(f"{event.event_id}: parent is not a conversation span")
                elif parent.context.simulation_id != event.context.simulation_id:
                    errors.append(f"{event.event_id}: parent is in another simulation")
            elif event.event_type is EventType.GATE:
                span = self._spans.get(event.run_id)
                if span is None or span.event_type is not EventType.TOOL_CALL:
                    errors.append(f"{event.event_id}: run {event.run_id} is not a tool-call span")
                elif span.tool_name != event.tool_name:
                    errors.append(f"{event.event_id}: tool_name differs from its tool-call span")
                elif span.context.simulation_id != event.context.simulation_id:
                    errors.append(f"{event.event_id}: tool-call span is in another simulation")
        return errors
