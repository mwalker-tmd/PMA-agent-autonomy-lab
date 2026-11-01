"""Find the ``trace-event/1`` spans a deterministic check cites.

A tool span holds no ``tool_call_id``, so it is matched to its call through the
result message's ``turn_index`` (docs/ARCHITECTURE.md, "Reading it correctly").
A span whose tool name, argument hash, or outcome disagrees with the call is not
evidence for it, and neither is a turn with several spans of a type: the check
then has nothing to cite, and it reports ``INSUFFICIENT_EVIDENCE``.
"""

from collections.abc import Iterable
from uuid import UUID

from agent_autonomy_lab.domain.events import EventType, TraceEvent, argument_hash
from agent_autonomy_lab.domain.tool_timeline import ToolCallRecord


class TraceEvidence:
    """Spans of one simulation's trace, by type and turn."""

    def __init__(self, events: Iterable[TraceEvent]) -> None:
        self._spans: dict[tuple[EventType, int], list[TraceEvent]] = {}
        self._roots: list[TraceEvent] = []
        self._tool_runs: dict[UUID, TraceEvent] = {}
        for event in events:
            if event.event_type is EventType.TOOL_CALL:
                self._tool_runs[event.run_id] = event
            if event.event_type is EventType.CONVERSATION:
                self._roots.append(event)
            if event.turn_index is not None:
                self._spans.setdefault((event.event_type, event.turn_index), []).append(event)

    def _only(self, event_type: EventType, turn_index: int) -> TraceEvent | None:
        spans = self._spans.get((event_type, turn_index), [])
        return spans[0] if len(spans) == 1 else None

    def conversation_span(self) -> TraceEvent | None:
        """The root span, which a result about the whole simulation cites."""
        return self._roots[0] if len(self._roots) == 1 else None

    def model_span(self, turn_index: int) -> TraceEvent | None:
        """The model call that produced this message; a message with no usage has none."""
        return self._only(EventType.MODEL_CALL, turn_index)

    def tool_span(self, record: ToolCallRecord) -> TraceEvent | None:
        """The tool span for this call, if the record and the trace agree on it."""
        result = record.result
        span = self._only(EventType.TOOL_CALL, result.turn_index) if result else None
        if (
            span is None
            or span.tool_name != record.call.name
            or span.argument_hash != argument_hash(record.call.arguments)
            or span.outcome is not result.tool_outcome
        ):
            return None
        return span

    def tool_run(self, run_id: UUID) -> TraceEvent | None:
        """The tool span a gate event annotates, whatever the record says about its call."""
        return self._tool_runs.get(run_id)

    def attempt_ids(self, record: ToolCallRecord) -> tuple[UUID, ...]:
        """Every event that shows this call was attempted: the model call and the tool span."""
        spans = (self.model_span(record.message.turn_index), self.tool_span(record))
        return tuple(span.event_id for span in spans if span is not None)
