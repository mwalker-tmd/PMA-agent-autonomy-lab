"""A ``TraceSink`` that writes trace events to LangSmith as threaded runs.

Each simulation becomes one trace (docs/ARCHITECTURE.md, "LangSmith thread
contract"): the ``conversation`` event is the root run, with top-level
``messages`` inputs and outputs so thread-level evaluators can read it; model
and tool calls are ``llm`` and ``tool`` child runs under it; gate events are
attached to their tool run's ``events``. Every run's metadata carries the
event's identifier fields, and LangSmith groups runs into a thread by the
``thread_id`` metadata key.

Events are buffered and exported at ``flush``, after the same trace-shape
checks ``InMemoryTraceSink.link_errors()`` runs, so a malformed trace is
rejected whole instead of reaching LangSmith in pieces. Span content is
exported as recorded; redaction is added in M3-06 (#76).
"""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from langsmith import Client

from agent_autonomy_lab.domain.events import (
    EventType,
    Outcome,
    TraceEvent,
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

# Event fields that become run fields instead of metadata.
_NOT_METADATA = frozenset({"inputs", "outputs", "run_id", "parent_run_id", "timestamp"})
_GATE_FIELDS = ("event_id", "gate_event", "decision", "decision_reason", "argument_hash")


class TraceShapeError(ValueError):
    """Recorded events do not form traces LangSmith can thread."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class LangSmithTraceSink:
    """Buffers trace events and exports them to a LangSmith project on ``flush``.

    ``client`` is a ``langsmith.Client``; tests pass one with a fake HTTP
    session, so no test needs network access. If ``flush`` raises
    ``TraceShapeError``, nothing is sent and the buffer is kept.
    """

    def __init__(self, client: Client, project_name: str) -> None:
        self._client = client
        self._project_name = project_name
        self._events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self._events.append(event)

    def flush(self) -> None:
        runs = to_runs(self._events, self._project_name)
        for run in runs:
            self._client.create_run(**run)
        self._client.flush()
        self._events.clear()


def to_runs(events: Sequence[TraceEvent], project_name: str) -> list[dict[str, Any]]:
    """Return ``Client.create_run`` keyword arguments for ``events``, roots first.

    Raises ``TraceShapeError`` if the events fail ``link_errors()`` or a
    conversation lacks well-formed top-level ``messages``.
    """
    errors = link_errors(events)
    for event in events:
        if event.event_type is EventType.CONVERSATION:
            errors += [f"{event.event_id}: {e}" for e in _conversation_errors(event)]
    if errors:
        raise TraceShapeError(errors)

    gates: dict[Any, list[TraceEvent]] = {}
    for event in events:
        if event.event_type is EventType.GATE:
            gates.setdefault(event.run_id, []).append(event)
    roots = {e.run_id: e for e in events if e.event_type is EventType.CONVERSATION}

    runs = []
    for span in parent_first(events):
        root = roots[span.parent_run_id] if span.parent_run_id else span
        runs.append(_run(span, root, gates.get(span.run_id, []), project_name))
    return runs


def _run(
    span: TraceEvent, root: TraceEvent, gates: Sequence[TraceEvent], project_name: str
) -> dict[str, Any]:
    dotted_order = _segment(span)
    if span is not root:
        dotted_order = f"{_segment(root)}.{dotted_order}"
    run: dict[str, Any] = {
        "name": _name(span),
        "run_type": RUN_TYPES[span.event_type],
        "project_name": project_name,
        "id": span.run_id,
        "trace_id": root.run_id,
        "parent_run_id": span.parent_run_id,
        "dotted_order": dotted_order,
        "start_time": span.timestamp,
        "end_time": span.timestamp + timedelta(milliseconds=span.elapsed_ms or 0),
        "inputs": json_data(span.inputs) or {},
        "outputs": _outputs(span),
        "extra": {"metadata": _metadata(span)},
    }
    if span.outcome is Outcome.ERROR:
        run["error"] = ": ".join(filter(None, (span.error_class, span.error_message))) or "error"
    if gates:
        run["events"] = [_gate_event(gate) for gate in gates]
    return run


def _segment(span: TraceEvent) -> str:
    """One ``dotted_order`` segment: the start time in UTC, then the run ID."""
    return f"{span.timestamp:%Y%m%dT%H%M%S%fZ}{span.run_id}"


def _name(span: TraceEvent) -> str:
    if span.event_type is EventType.TOOL_CALL:
        return span.tool_name or "tool_call"
    return span.event_type.value


def _outputs(span: TraceEvent) -> dict[str, Any]:
    """Span outputs, with token usage and cost where LangSmith reads them for LLM runs."""
    outputs = json_data(span.outputs) or {}
    if span.event_type is EventType.MODEL_CALL and span.tokens and "usage_metadata" not in outputs:
        usage = {
            "input_tokens": span.tokens.prompt_tokens,
            "output_tokens": span.tokens.completion_tokens,
            "total_tokens": span.tokens.total_tokens,
        }
        if span.cost is not None:
            usage["total_cost"] = span.cost
        outputs["usage_metadata"] = usage
    return outputs


def _metadata(span: TraceEvent) -> dict[str, Any]:
    return {
        key: value
        for key, value in span.to_dict().items()
        if value is not None and key not in _NOT_METADATA
    }


def _gate_event(gate: TraceEvent) -> dict[str, Any]:
    record = gate.to_dict()
    return {
        "name": f"gate:{gate.gate_event}",
        "time": gate.timestamp.isoformat(),
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
    if role not in MESSAGE_ROLES:
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
