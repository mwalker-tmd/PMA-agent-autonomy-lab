import json
from dataclasses import replace

import pytest

from agent_autonomy_lab.adapters.langsmith.trace_sink import message_errors
from agent_autonomy_lab.adapters.tau2 import read_results
from agent_autonomy_lab.application.export_simulation import export_run, simulation_trace
from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Outcome,
    argument_hash,
    policy_version,
    thread_id_for,
)
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink
from tests.conftest import DENVER, NOT_EXPORTED, SAMPLE_RUN, SAMPLE_SIMULATION_ID

BASELINE = ExperimentClass.OFFICIAL_UPSTREAM_BASELINE


@pytest.fixture
def run():
    return read_results(SAMPLE_RUN, DENVER)


@pytest.fixture
def sink(run):
    sink = InMemoryTraceSink()
    export_run(run, sink, BASELINE)
    return sink


def test_each_simulation_is_one_well_formed_thread(run, sink):
    (root,) = sink.roots()
    assert sink.link_errors() == []
    assert sink.flush_count == 1
    assert root.context.simulation_id == SAMPLE_SIMULATION_ID
    assert root.context.thread_id == thread_id_for(SAMPLE_SIMULATION_ID)
    assert root.elapsed_ms == 8500.0


def test_run_context_records_provenance(run, sink):
    context = sink.roots()[0].context
    assert context.task_id == "0"
    assert context.autonomy_level == NO_GATE
    assert context.experiment_class is BASELINE
    assert context.agent_version == "llm_agent"
    assert context.model_id == context.user_model_id == "openai/responses/gpt-6-luna"
    assert context.policy_version == policy_version(run.policy_text)


def test_conversation_messages_are_the_whole_transcript(sink):
    root = sink.roots()[0].to_dict()
    inputs, outputs = root["inputs"]["messages"], root["outputs"]["messages"]
    assert [m["role"] for m in inputs] == ["assistant", "user"]
    assert [m["role"] for m in outputs] == ["assistant", "tool", "assistant", "user"]
    for message in inputs + outputs:
        assert message_errors(message) == []
    call = outputs[0]["tool_calls"][0]
    assert call["function"]["name"] == "get_order_details"
    assert json.loads(call["function"]["arguments"]) == {"order_id": "#W0000001"}
    assert outputs[1]["tool_call_id"] == call["id"]


def test_every_model_output_is_a_model_call_and_the_opener_is_not(sink):
    root = sink.roots()[0]
    models = [e for e in sink.children(root.run_id) if e.event_type is EventType.MODEL_CALL]
    assert [m.turn_index for m in models] == [1, 2, 4, 5]
    agent_call = models[1]
    assert agent_call.elapsed_ms == 1500.0
    assert agent_call.tokens.prompt_tokens == 3600 and agent_call.cost == 0.0004
    assert agent_call.to_dict()["outputs"]["messages"][0]["tool_calls"][0]["id"] == "call_1"


def test_tool_result_is_a_tool_call_with_its_outcome(sink):
    root = sink.roots()[0]
    (tool,) = [e for e in sink.children(root.run_id) if e.event_type is EventType.TOOL_CALL]
    assert tool.tool_name == "get_order_details"
    assert tool.argument_hash == argument_hash({"order_id": "#W0000001"})
    assert tool.outcome is Outcome.ERROR
    assert tool.error_message == "Error: Order not found"
    assert tool.turn_index == 3


def test_reward_is_feedback_on_the_root_not_a_message(sink):
    root = sink.roots()[0]
    feedback = {f.key: f for f in sink.feedback_for(root.run_id)}
    assert {k: f.score for k, f in feedback.items()} == {
        "reward": 0.0, "reward_db": 1.0, "reward_nl_assertion": 0.0,
    }  # fmt: skip
    value = feedback["reward"].value
    assert value["reward_breakdown"] == {"DB": 1.0, "NL_ASSERTION": 0.0}
    assert value["nl_assertions"][0]["met"] is False
    messages = json.dumps(root.to_dict()["inputs"]) + json.dumps(root.to_dict()["outputs"])
    assert "reward" not in messages and "nl_assertion" not in messages


def test_nothing_unexported_reaches_an_event_or_feedback(sink):
    exported = json.dumps([e.to_dict() for e in sink.events]) + repr(sink.feedback)
    for marker in NOT_EXPORTED:
        assert marker not in exported


def test_exporting_again_gives_the_same_ids(run):
    first, second = InMemoryTraceSink(), InMemoryTraceSink()
    export_run(run, first, BASELINE)
    export_run(run, second, BASELINE)
    assert [e.to_dict() for e in first.events] == [e.to_dict() for e in second.events]
    assert [f.feedback_id for f in first.feedback] == [f.feedback_id for f in second.feedback]


def test_run_ids_are_uuid7_ordered_by_start_time(sink):
    spans = sink.events
    assert all(span.run_id.version == 7 for span in spans)
    root = sink.roots()[0]
    children = sink.children(root.run_id)
    assert sorted(children, key=lambda e: e.run_id) == sorted(children, key=lambda e: e.timestamp)


def test_tool_result_without_a_call_is_rejected(run):
    simulation = run.simulations[0]
    messages = tuple(m for m in simulation.messages if not m.tool_calls)
    with pytest.raises(ValueError, match="answers no tool call"):
        simulation_trace(run, replace(simulation, messages=messages), BASELINE)


def test_simulation_without_a_reward_gets_no_feedback(run):
    simulation = replace(run.simulations[0], reward=None)
    _, feedback = simulation_trace(run, simulation, BASELINE)
    assert feedback == []


def conversation_roles(run, messages):
    simulation = replace(run.simulations[0], messages=messages, reward=None)
    events, _ = simulation_trace(run, simulation, BASELINE)
    record = events[0].to_dict()
    return (
        [m["role"] for m in record["inputs"]["messages"]],
        [m["role"] for m in record["outputs"]["messages"]],
    )


def test_transcript_ending_at_the_first_customer_message_keeps_both_sides(run):
    opener, ask, *_ = run.simulations[0].messages
    assert conversation_roles(run, (opener, ask)) == (["assistant"], ["user"])


def test_transcript_without_a_customer_message_keeps_both_sides(run):
    opener, _, call, *_ = run.simulations[0].messages
    reply = replace(call, tool_calls=(), content="Are you still there?")
    assert conversation_roles(run, (opener, reply)) == (["assistant"], ["assistant"])


@pytest.mark.parametrize("count", [0, 1])
def test_too_short_simulation_is_skipped_and_reported(run, count):
    short = replace(
        run.simulations[0], simulation_id="short-sim", messages=run.simulations[0].messages[:count]
    )
    sink = InMemoryTraceSink()
    skipped, exported = export_run(
        replace(run, simulations=(short, run.simulations[0])), sink, BASELINE
    )
    assert skipped.skipped == f"{count} message(s); a conversation needs 2"
    assert (skipped.spans, skipped.feedback, skipped.root_run_id) == (0, 0, None)
    assert exported.skipped is None and exported.spans == 6
    assert [root.context.simulation_id for root in sink.roots()] == [SAMPLE_SIMULATION_ID]
    assert sink.link_errors() == []
    with pytest.raises(ValueError, match="cannot form a conversation"):
        simulation_trace(run, short, BASELINE)
