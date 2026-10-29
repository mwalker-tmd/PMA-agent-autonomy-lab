"""A ``TraceSink`` that writes trace events to LangSmith as threaded runs.

Each simulation becomes one trace (docs/ARCHITECTURE.md, "LangSmith thread
contract"): the ``conversation`` event is the root run, with top-level
``messages`` inputs and outputs so thread-level evaluators can read it; model
and tool calls are ``llm`` and ``tool`` child runs under it; gate events are
attached to their tool run's ``events``. Every run's metadata carries the
event's identifier fields, and LangSmith groups runs into a thread by the
``thread_id`` metadata key. Feedback becomes LangSmith feedback on its run.

Events and feedback are buffered and exported at ``flush``, after the same
trace-shape checks ``InMemoryTraceSink.link_errors()`` runs, so a malformed
trace is rejected whole instead of reaching LangSmith in pieces. Secrets are
removed from every run and feedback item as it is sent (``redaction``): span
content, metadata, errors, gate events, and feedback values and comments.

LangSmith rejects a run whose start time is more than 24 hours from now. A
trace recorded earlier, such as a saved simulation exported later, is shifted
whole so its root starts at export time and every span keeps its offset from
the root; each run's metadata keeps the event's real ``timestamp`` (ADR-017).
Before shifting, the sink asks LangSmith which of the trace's runs it already
holds. If the root is there, its stored start time is the anchor and only the
missing runs are sent, so a retry after a partial upload stays consistent with
what was stored. A missing run that would still fall outside the window
raises ``StaleTraceError`` before anything is sent.

Run and feedback IDs come from the events, so sending the same events again
does not duplicate anything: LangSmith answers a known run ID with a 409
conflict, which the SDK ignores, and replaces feedback that has a known ID.
With ``auto_batch_tracing`` on, the SDK sends runs in the background and only
logs a rejected run, so a caller that must know every run arrived passes a
client with ``auto_batch_tracing=False``; then a rejected run raises.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from langsmith import Client
from langsmith.utils import LangSmithNotFoundError

from agent_autonomy_lab.adapters.langsmith.redaction import Redactor
from agent_autonomy_lab.domain.events import (
    EventType,
    Feedback,
    Outcome,
    RunContext,
    TraceEvent,
    feedback_errors,
    json_data,
    link_errors,
    parent_first,
)

RUN_TYPES = {
    EventType.CONVERSATION: "chain",
    EventType.MODEL_CALL: "llm",
    EventType.TOOL_CALL: "tool",
}

MESSAGE_ROLES = frozenset({"system", "user", "assistant", "tool"})

# Event fields that become run fields instead of metadata. ``timestamp`` stays in
# metadata: a shifted run's start time is not when the event happened.
_NOT_METADATA = frozenset({"inputs", "outputs", "run_id", "parent_run_id"})

# LangSmith accepts start times within 24 hours of now. A trace is shifted when its
# root is more than 23 hours away, leaving an hour for the upload and clock skew.
LANGSMITH_WINDOW = timedelta(hours=24)
MAX_CLOCK_DISTANCE = timedelta(hours=23)
_GATE_FIELDS = ("event_id", "gate_event", "decision", "decision_reason", "argument_hash")


class TraceShapeError(ValueError):
    """Recorded events do not form traces LangSmith can thread."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class StaleTraceError(RuntimeError):
    """Runs missing from LangSmith cannot be sent within its start-time window.

    Raised when a trace was partly stored more than a day ago: its stored
    root fixes the missing runs' start times, and those are now too old.
    """

    def __init__(self, run_ids: Sequence[UUID]) -> None:
        self.run_ids = list(run_ids)
        super().__init__(
            f"{len(self.run_ids)} missing run(s) would start more than 24 hours from now, "
            f"anchored to a stored root: {', '.join(map(str, self.run_ids))}"
        )


# Given a trace ID, the IDs and start times of the runs LangSmith holds for it.
StoredRuns = Callable[[UUID], Mapping[UUID, datetime]]


class LangSmithTraceSink:
    """Buffers trace events and feedback and exports them to a LangSmith project on ``flush``.

    ``client`` is a ``langsmith.Client``; tests pass one with a fake HTTP
    session, so no test needs network access. ``stored_runs`` looks up a
    trace's stored runs; it defaults to a LangSmith query, and tests pass a
    fake. ``redactor`` removes secrets before anything is sent; it defaults
    to ``Redactor.from_environment()``. If ``flush`` raises
    ``TraceShapeError`` or ``StaleTraceError``, nothing is sent and the
    buffer is kept.
    """

    def __init__(
        self,
        client: Client,
        project_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        stored_runs: StoredRuns | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._client = client
        self._project_name = project_name
        self._clock = clock
        self._stored_runs = stored_runs or self._query_stored_runs
        self._redact = redactor or Redactor.from_environment()
        self._events: list[TraceEvent] = []
        self._feedback: list[Feedback] = []

    def record(self, event: TraceEvent) -> None:
        self._events.append(event)

    def record_feedback(self, feedback: Feedback) -> None:
        self._feedback.append(feedback)

    def flush(self) -> None:
        _check_shape(self._events, self._feedback)
        now = self._clock()
        anchors, stored = self._stored_state(now)
        runs = to_runs(self._events, self._project_name, now=now, anchors=anchors)
        runs = [run for run in runs if run["id"] not in stored]
        late = [run["id"] for run in runs if abs(run["start_time"] - now) > LANGSMITH_WINDOW]
        if late:
            raise StaleTraceError(late)
        for run in runs:
            self._client.create_run(**redact_run(run, self._redact))
        self._client.flush()
        if self._feedback:
            project_id = self._project_id()
            for item in to_feedback(self._events, self._feedback):
                self._client.create_feedback(
                    **redact_feedback(item, self._redact), session_id=project_id
                )
            self._client.flush()
        self._events.clear()
        self._feedback.clear()

    def _stored_state(self, now: datetime) -> tuple[dict[UUID, datetime], set[UUID]]:
        """For each trace that needs a shift, its stored root's start time and stored runs.

        A trace within the window needs no lookup: its times are sent as
        recorded, and LangSmith ignores runs it already holds.
        """
        anchors: dict[UUID, datetime] = {}
        stored: set[UUID] = set()
        for root in self._events:
            if root.event_type is not EventType.CONVERSATION:
                continue
            if not _shift(root.timestamp, now):
                continue
            found = self._stored_runs(root.run_id)
            stored.update(found)
            if root.run_id in found:
                anchors[root.run_id] = found[root.run_id]
        return anchors, stored

    def _query_stored_runs(self, trace_id: UUID) -> dict[UUID, datetime]:
        """Query LangSmith for a trace's runs (the SDK's runs API is async-only)."""
        project_id = str(self._project_id())

        async def query() -> dict[UUID, datetime]:
            runs = self._client.runs.query_v2(
                project_ids=[project_id],
                trace_id=str(trace_id),
                selects=["id", "start_time"],
                page_size=100,
            )
            return {UUID(str(run.id)): run.start_time async for run in runs}

        return asyncio.run(query())

    def _project_id(self) -> Any:
        """The project's ID, which LangSmith needs to find a run for feedback."""
        try:
            return self._client.read_project(project_name=self._project_name).id
        except LangSmithNotFoundError:
            return self._client.create_project(self._project_name).id


def to_runs(
    events: Sequence[TraceEvent],
    project_name: str,
    now: datetime | None = None,
    anchors: Mapping[UUID, datetime] | None = None,
) -> list[dict[str, Any]]:
    """Return ``Client.create_run`` keyword arguments for ``events``, roots first.

    With ``now``, a trace whose root starts more than ``MAX_CLOCK_DISTANCE``
    from it is shifted to start at ``now``, or at its root's entry in
    ``anchors`` (the start time LangSmith already holds for it). Raises
    ``TraceShapeError`` if the events fail ``link_errors()`` or a
    conversation lacks well-formed top-level ``messages``.
    """
    _check_shape(events, ())

    gates: dict[Any, list[TraceEvent]] = {}
    for event in events:
        if event.event_type is EventType.GATE:
            gates.setdefault(event.run_id, []).append(event)
    roots = {e.run_id: e for e in events if e.event_type is EventType.CONVERSATION}

    runs = []
    for span in parent_first(events):
        root = roots[span.parent_run_id] if span.parent_run_id else span
        if anchors and root.run_id in anchors:
            shift = anchors[root.run_id] - root.timestamp
        else:
            shift = _shift(root.timestamp, now)
        runs.append(_run(span, root, gates.get(span.run_id, []), project_name, shift))
    return runs


# ``create_run`` and ``create_feedback`` arguments that can carry recorded text.
_RUN_CONTENT = ("inputs", "outputs", "extra", "error", "events")
_FEEDBACK_CONTENT = ("value", "comment")


def redact_run(run: Mapping[str, Any], redactor: Redactor) -> dict[str, Any]:
    """A copy of ``create_run`` arguments with secrets removed from their content."""
    return {k: redactor(v) if k in _RUN_CONTENT else v for k, v in run.items()}


def redact_feedback(item: Mapping[str, Any], redactor: Redactor) -> dict[str, Any]:
    """A copy of ``create_feedback`` arguments with secrets removed from their content."""
    return {k: redactor(v) if k in _FEEDBACK_CONTENT else v for k, v in item.items()}


def _shift(root_start: datetime, now: datetime | None) -> timedelta:
    """How far to move a trace so LangSmith accepts its start times."""
    if now is None or abs(now - root_start) <= MAX_CLOCK_DISTANCE:
        return timedelta(0)
    return now - root_start


def to_feedback(events: Sequence[TraceEvent], feedback: Sequence[Feedback]) -> list[dict[str, Any]]:
    """Return ``Client.create_feedback`` keyword arguments, without the project ID.

    Raises ``TraceShapeError`` if the feedback fails ``feedback_errors()``.
    """
    _check_shape(events, feedback)
    spans = {e.run_id: e for e in events if e.is_span}
    return [
        {
            "run_id": item.run_id,
            "trace_id": spans[item.run_id].parent_run_id or item.run_id,
            "key": item.key,
            "score": item.score,
            "value": json_data(item.value),
            "comment": item.comment,
            "feedback_id": item.feedback_id,
            "feedback_source_type": "api",
            "source_info": {
                "schema_version": item.schema_version,
                **_context_metadata(item.context),
            },
        }
        for item in feedback
    ]


def shape_errors(events: Sequence[TraceEvent], feedback: Sequence[Feedback]) -> list[str]:
    """Every reason ``flush`` would refuse these events and feedback, checked offline.

    ``link_errors()`` and ``feedback_errors()``, plus the lab's rule that a
    conversation's top-level ``messages`` follow the OpenAI chat format that
    LangSmith thread evaluators read. LangSmith itself accepts any JSON
    content; the format rule is ours.
    """
    errors = link_errors(events) + feedback_errors(events, feedback)
    for event in events:
        if event.event_type is EventType.CONVERSATION:
            errors += [f"{event.event_id}: {e}" for e in _conversation_errors(event)]
    return errors


def _check_shape(events: Sequence[TraceEvent], feedback: Sequence[Feedback]) -> None:
    if errors := shape_errors(events, feedback):
        raise TraceShapeError(errors)


def _context_metadata(context: RunContext) -> dict[str, Any]:
    """The run context's non-null fields, as LangSmith metadata."""
    fields = {name: getattr(context, name) for name in RunContext.__dataclass_fields__}
    return {
        name: value.value if hasattr(value, "value") else value
        for name, value in fields.items()
        if value is not None
    }


def _run(
    span: TraceEvent,
    root: TraceEvent,
    gates: Sequence[TraceEvent],
    project_name: str,
    shift: timedelta,
) -> dict[str, Any]:
    start = span.timestamp + shift
    dotted_order = _segment(start, span)
    if span is not root:
        dotted_order = f"{_segment(root.timestamp + shift, root)}.{dotted_order}"
    run: dict[str, Any] = {
        "name": _name(span),
        "run_type": RUN_TYPES[span.event_type],
        "project_name": project_name,
        "id": span.run_id,
        "trace_id": root.run_id,
        "parent_run_id": span.parent_run_id,
        "dotted_order": dotted_order,
        "start_time": start,
        "end_time": start + timedelta(milliseconds=span.elapsed_ms or 0),
        "inputs": json_data(span.inputs) or {},
        "outputs": _outputs(span),
        "extra": {"metadata": _metadata(span)},
    }
    if span.outcome is Outcome.ERROR:
        run["error"] = ": ".join(filter(None, (span.error_class, span.error_message))) or "error"
    if gates:
        run["events"] = [_gate_event(gate, shift) for gate in gates]
    return run


def _segment(start: datetime, span: TraceEvent) -> str:
    """One ``dotted_order`` segment: the start time in UTC, then the run ID."""
    return f"{start:%Y%m%dT%H%M%S%fZ}{span.run_id}"


def _name(span: TraceEvent) -> str:
    if span.event_type is EventType.TOOL_CALL:
        return span.tool_name or "tool_call"
    return span.event_type.value


def _outputs(span: TraceEvent) -> dict[str, Any]:
    """Span outputs, with token usage and cost where LangSmith reads them for LLM runs."""
    outputs = json_data(span.outputs) or {}
    if span.event_type is not EventType.MODEL_CALL or "usage_metadata" in outputs:
        return outputs
    usage: dict[str, Any] = {}
    if span.tokens is not None:
        usage["input_tokens"] = span.tokens.prompt_tokens
        usage["output_tokens"] = span.tokens.completion_tokens
        usage["total_tokens"] = span.tokens.total_tokens
    if span.cost is not None:
        usage["total_cost"] = span.cost
    if usage:
        outputs["usage_metadata"] = usage
    return outputs


def _metadata(span: TraceEvent) -> dict[str, Any]:
    return {
        key: value
        for key, value in span.to_dict().items()
        if value is not None and key not in _NOT_METADATA
    }


def _gate_event(gate: TraceEvent, shift: timedelta) -> dict[str, Any]:
    record = gate.to_dict()
    return {
        "name": f"gate:{gate.gate_event}",
        "time": (gate.timestamp + shift).isoformat(),
        "kwargs": {key: record[key] for key in _GATE_FIELDS if record[key] is not None},
    }


def _conversation_errors(event: TraceEvent) -> list[str]:
    errors = []
    for name, content in (("inputs", event.inputs), ("outputs", event.outputs)):
        messages = content.get("messages") if content is not None else None
        if not isinstance(messages, Sequence) or isinstance(messages, str) or not messages:
            errors.append(f"conversation {name} need a non-empty top-level messages list")
            continue
        for index, message in enumerate(messages):
            errors += [f"{name}.messages[{index}]: {e}" for e in message_errors(message)]
    return errors


def message_errors(message: Any) -> list[str]:
    """Describe how ``message`` departs from the OpenAI chat format LangSmith threads read.

    A message has a ``role``; ``content`` is a string or null; an assistant
    message has content or ``tool_calls`` (each with an ``id`` and a
    ``function`` with a ``name`` and JSON-string ``arguments``); a tool
    message has a ``tool_call_id``.
    """
    if not isinstance(message, Mapping):
        return ["message must be an object"]
    errors = []
    role = message.get("role")
    if not isinstance(role, str) or role not in MESSAGE_ROLES:
        errors.append(f"role must be one of {sorted(MESSAGE_ROLES)}")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        errors.append("content must be a string or null")
    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        if role != "assistant":
            errors.append("only assistant messages have tool_calls")
        elif not isinstance(tool_calls, Sequence) or isinstance(tool_calls, str):
            errors.append("tool_calls must be a list")
        else:
            for index, call in enumerate(tool_calls):
                errors += [f"tool_calls[{index}]: {e}" for e in _tool_call_errors(call)]
    if role == "assistant" and content is None and not tool_calls:
        errors.append("an assistant message needs content or tool_calls")
    if role in ("system", "user", "tool") and content is None:
        errors.append(f"a {role} message needs content")
    if role == "tool" and not _non_empty_str(message.get("tool_call_id")):
        errors.append("a tool message needs a tool_call_id")
    return errors


def _tool_call_errors(call: Any) -> list[str]:
    if not isinstance(call, Mapping):
        return ["tool call must be an object"]
    errors = []
    if not _non_empty_str(call.get("id")):
        errors.append("needs an id")
    if call.get("type", "function") != "function":
        errors.append("type must be function")
    function = call.get("function")
    if not isinstance(function, Mapping) or not _non_empty_str(function.get("name")):
        errors.append("needs a function with a name")
    elif not isinstance(function.get("arguments"), str):
        errors.append("function arguments must be a JSON string")
    return errors


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
