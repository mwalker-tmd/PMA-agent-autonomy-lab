"""The ``TraceSink`` port and its in-memory test double."""

from typing import Protocol
from uuid import UUID

from agent_autonomy_lab.domain.events import (
    EventType,
    Feedback,
    TraceEvent,
    feedback_errors,
    link_errors,
)


class TraceSink(Protocol):
    """Records trace events, and feedback on their spans, for a trace backend.

    Spans are recorded when they finish, so a child is usually recorded before
    its parent; a sink must not require parents first. Feedback may be
    recorded before the span it scores. ``flush`` blocks until every recorded
    event and feedback item has been delivered. A sink that exports whole
    traces may raise ``ValueError`` from ``flush`` when the recorded events
    fail ``link_errors()`` or the feedback fails ``feedback_errors()``.
    """

    def record(self, event: TraceEvent) -> None: ...

    def record_feedback(self, feedback: Feedback) -> None: ...

    def flush(self) -> None: ...


class InMemoryTraceSink:
    """Keeps events and feedback in lists and checks the identifier contract on demand."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.feedback: list[Feedback] = []
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

    def record_feedback(self, feedback: Feedback) -> None:
        self.feedback.append(feedback)

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

    def feedback_for(self, run_id: UUID) -> list[Feedback]:
        """Feedback on the span ``run_id``, in recording order."""
        return [f for f in self.feedback if f.run_id == run_id]

    def roots(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type is EventType.CONVERSATION]

    def link_errors(self) -> list[str]:
        """Describe every broken link; an empty list means the trace is well formed.

        See ``link_errors`` and ``feedback_errors`` in ``domain.events.trace_shape``
        for the checks; trace backend adapters run the same checks before they
        export.
        """
        return link_errors(self.events) + feedback_errors(self.events, self.feedback)
