import pytest

from agent_autonomy_lab.domain.events import (
    EventType,
    GateEventClass,
    Outcome,
    argument_hash,
    new_id,
)
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink
from tests.conftest import make_context, make_event


def record_conversation(sink, context):
    """Record one conversation in finish order: children before the root."""
    root = new_id()
    model = make_event(EventType.MODEL_CALL, context, parent_run_id=root, turn_index=1)
    tool_run = new_id()
    args = {"order_id": "#W2378156"}
    attempted = make_event(
        EventType.GATE, context, run_id=tool_run, gate_event=GateEventClass.ATTEMPTED
    )
    tool = make_event(
        EventType.TOOL_CALL,
        context,
        run_id=tool_run,
        parent_run_id=root,
        turn_index=2,
        argument_hash=argument_hash(args),
        outcome=Outcome.SUCCESS,
    )
    executed = make_event(
        EventType.GATE, context, run_id=tool_run, gate_event=GateEventClass.EXECUTED
    )
    conversation = make_event(EventType.CONVERSATION, context, run_id=root)
    for event in (model, attempted, tool, executed, conversation):
        sink.record(event)
    return conversation, model, tool, (attempted, executed)


def test_in_memory_sink_satisfies_the_port():
    sink: TraceSink = InMemoryTraceSink()
    sink.record(make_event(EventType.CONVERSATION))
    sink.flush()
    assert sink.flush_count == 1


def test_parent_child_links(context):
    sink = InMemoryTraceSink()
    conversation, model, tool, gates = record_conversation(sink, context)
    assert sink.link_errors() == []
    assert sink.roots() == [conversation]
    assert sink.children(conversation.run_id) == [model, tool]
    assert sink.children(model.run_id) == []
    assert sink.gate_events(tool.run_id) == list(gates)
    assert sink.span(tool.run_id) is tool


def test_events_keep_recording_order(context):
    sink = InMemoryTraceSink()
    record_conversation(sink, context)
    assert [e.event_type for e in sink.events] == [
        EventType.MODEL_CALL, EventType.GATE, EventType.TOOL_CALL, EventType.GATE,
        EventType.CONVERSATION,
    ]  # fmt: skip


def test_identifiers_correlate_across_a_conversation(context):
    sink = InMemoryTraceSink()
    record_conversation(sink, context)
    records = [e.to_dict() for e in sink.events]
    assert {r["simulation_id"] for r in records} == {"sim-00023"}
    assert {r["thread_id"] for r in records} == {context.thread_id}
    assert len({r["event_id"] for r in records}) == len(records)


def test_two_simulations_are_separate_threads():
    sink = InMemoryTraceSink()
    first, *_ = record_conversation(sink, make_context("sim-a"))
    second, *_ = record_conversation(sink, make_context("sim-b"))
    assert sink.link_errors() == []
    assert first.context.thread_id != second.context.thread_id
    assert len(sink.roots()) == 2


def test_duplicate_event_id_is_rejected():
    sink = InMemoryTraceSink()
    event = make_event(EventType.CONVERSATION)
    sink.record(event)
    with pytest.raises(ValueError, match="duplicate event_id"):
        sink.record(event)


def test_duplicate_span_run_id_is_rejected():
    sink = InMemoryTraceSink()
    run_id = new_id()
    sink.record(make_event(EventType.CONVERSATION, run_id=run_id))
    with pytest.raises(ValueError, match="duplicate span"):
        sink.record(make_event(EventType.CONVERSATION, run_id=run_id))


def test_missing_parent_is_reported():
    sink = InMemoryTraceSink()
    orphan = make_event(EventType.MODEL_CALL)
    sink.record(orphan)
    assert sink.link_errors() == [f"{orphan.event_id}: parent {orphan.parent_run_id} not recorded"]


def test_parent_in_another_simulation_is_reported():
    sink = InMemoryTraceSink()
    root = make_event(EventType.CONVERSATION, make_context("sim-a"))
    child = make_event(EventType.MODEL_CALL, make_context("sim-b"), parent_run_id=root.run_id)
    sink.record(root)
    sink.record(child)
    assert sink.link_errors() == [f"{child.event_id}: parent is in another simulation"]


def test_gate_event_must_point_at_a_tool_call(context):
    sink = InMemoryTraceSink()
    root = make_event(EventType.CONVERSATION, context)
    gate = make_event(
        EventType.GATE, context, run_id=root.run_id, gate_event=GateEventClass.ATTEMPTED
    )
    sink.record(root)
    sink.record(gate)
    assert sink.link_errors() == [f"{gate.event_id}: run {root.run_id} is not a tool-call span"]


def test_gate_event_tool_must_match_its_span(context):
    sink = InMemoryTraceSink()
    root = make_event(EventType.CONVERSATION, context)
    tool = make_event(EventType.TOOL_CALL, context, parent_run_id=root.run_id)
    gate = make_event(
        EventType.GATE,
        context,
        run_id=tool.run_id,
        tool_name="cancel_pending_order",
        gate_event=GateEventClass.ATTEMPTED,
    )
    for event in (root, tool, gate):
        sink.record(event)
    assert sink.link_errors() == [f"{gate.event_id}: tool_name differs from its tool-call span"]


def test_parent_must_be_a_conversation(context):
    sink = InMemoryTraceSink()
    first_run, second_run = new_id(), new_id()
    first = make_event(EventType.MODEL_CALL, context, run_id=first_run, parent_run_id=second_run)
    second = make_event(EventType.MODEL_CALL, context, run_id=second_run, parent_run_id=first_run)
    sink.record(first)
    sink.record(second)
    assert sink.link_errors() == [
        f"{first.event_id}: parent is not a conversation span",
        f"{second.event_id}: parent is not a conversation span",
    ]
