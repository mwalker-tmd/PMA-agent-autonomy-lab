from datetime import timedelta

import pytest

from agent_autonomy_lab.adapters.langsmith import shape_errors
from agent_autonomy_lab.application.trace_live_run import live_trace, record_live_run
from agent_autonomy_lab.domain.agent_turns import AgentFingerprint, AgentTurn, LiveRecording
from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Outcome,
    TokenUsage,
    TrafficType,
    argument_hash,
    thread_id_for,
)
from agent_autonomy_lab.domain.simulations import Message, Reward, SavedSimulation, ToolCall
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink
from tests.conftest import T0

SIM_ID = "11111111-1111-4111-8111-111111111111"
SENT = AgentFingerprint(
    model_id="openai/responses/gpt-6-luna",
    prompt_version="prompt-aaaaaaaaaaaa",
    tool_schema_version="tools-bbbbbbbbbbbb",
    model_args_version="args-cccccccccccc",
    model_args={"reasoning_effort": "medium", "seed": 670487},
)
CALL = ToolCall(call_id="call-1", name="find_user_id_by_name_zip", arguments={"zip": "19122"})


def at(seconds: float):
    return T0 + timedelta(seconds=seconds)


def transcript() -> tuple[Message, ...]:
    return (
        Message(role="assistant", turn_index=0, timestamp=at(0), content="Hi!"),
        Message(
            role="user",
            turn_index=1,
            timestamp=at(2),
            content="Exchange my keyboard.",
            tokens=TokenUsage(50, 20),
            cost=0.0001,
        ),
        Message(
            role="assistant",
            turn_index=2,
            timestamp=at(4),
            tool_calls=(CALL,),
            tokens=TokenUsage(800, 30),
            cost=0.0003,
            generation_seconds=1.5,
        ),
        Message(
            role="tool", turn_index=3, timestamp=at(4.1), content="user_1", tool_call_id="call-1"
        ),
        Message(
            role="assistant",
            turn_index=4,
            timestamp=at(6),
            content="Found you.",
            tokens=TokenUsage(900, 10),
            cost=0.0002,
        ),
        Message(role="user", turn_index=5, timestamp=at(8), content="###STOP###"),
    )


def recording(turns=None, messages=None) -> LiveRecording:
    messages = messages or transcript()
    turns = (
        turns
        if turns is not None
        else (
            AgentTurn(2, at(2.4), 1600.0, SENT, inputs=(messages[1],), output=messages[2]),
            AgentTurn(4, at(4.2), 1700.0, SENT, inputs=(messages[3],), output=messages[4]),
        )
    )
    return LiveRecording(
        agent_version="lab_traced_llm_agent",
        started_at=at(0),
        ended_at=at(8.5),
        transcript=messages,
        turns=turns,
        overhead_ms=1.25,
    )


REWARD = Reward(1.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": True}})


def simulation(reward=REWARD):
    return SavedSimulation(
        simulation_id=SIM_ID,
        task_id="0",
        start_time=at(0),
        duration_seconds=8.5,
        termination_reason="user_stop",
        messages=transcript(),
        reward=reward,
    )


def trace(**overrides):
    return live_trace(
        overrides.pop("recording", recording()),
        overrides.pop("simulation", simulation()),
        user_model_id="openai/responses/gpt-6-luna",
        policy_version="policy-2c9652afbce5",
    )


def by_type(events, event_type):
    return [e for e in events if e.event_type is event_type]


def test_trace_is_one_well_formed_thread_linked_to_the_simulation():
    events, feedback = trace()
    assert shape_errors(events, feedback) == []
    root = events[0]
    assert root.event_type is EventType.CONVERSATION
    ctx = root.context
    assert ctx.simulation_id == SIM_ID
    assert ctx.thread_id == thread_id_for(SIM_ID)
    assert ctx.task_id == "0"
    assert all(e.context == ctx for e in events)


def test_runs_are_labeled_as_a_synthetic_ungated_lab_experiment():
    ctx = trace()[0][0].context
    assert ctx.experiment_class is ExperimentClass.CUSTOM_LAB_EXPERIMENT
    assert ctx.traffic_type is TrafficType.SYNTHETIC
    assert ctx.autonomy_level == NO_GATE
    assert ctx.agent_version == "lab_traced_llm_agent"
    assert ctx.model_id == SENT.model_id
    assert ctx.prompt_version == SENT.prompt_version
    assert ctx.tool_schema_version == SENT.tool_schema_version
    assert ctx.policy_version == "policy-2c9652afbce5"
    assert ctx.user_model_id == "openai/responses/gpt-6-luna"


def test_root_holds_the_whole_transcript_timed_by_the_wrapper():
    root = trace()[0][0]
    messages = root.inputs["messages"] + root.outputs["messages"]
    assert [m["role"] for m in messages] == [
        "assistant",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert root.timestamp == at(0)
    assert root.elapsed_ms == pytest.approx(8500)


def test_every_agent_turn_is_a_timed_model_call_with_usage():
    events, _ = trace()
    agent_calls = [
        e for e in by_type(events, EventType.MODEL_CALL) if "model_args" in (e.inputs or {})
    ]
    first, second = agent_calls
    assert first.turn_index == 2
    assert first.timestamp == at(2.4)
    assert first.elapsed_ms == 1600.0
    assert first.tokens == TokenUsage(800, 30)
    assert first.cost == 0.0003
    assert first.outcome is Outcome.SUCCESS
    assert first.to_dict()["inputs"]["messages"] == [
        {"role": "user", "content": "Exchange my keyboard."}
    ]
    assert first.inputs["model"] == SENT.model_id
    assert dict(first.inputs["model_args"]) == {"reasoning_effort": "medium", "seed": 670487}
    assert first.outputs["messages"][0]["tool_calls"][0]["function"]["name"] == CALL.name
    assert second.inputs["messages"][0]["role"] == "tool"


def test_simulated_user_calls_are_model_calls_too():
    events, _ = trace()
    user_calls = [
        e for e in by_type(events, EventType.MODEL_CALL) if "model_args" not in (e.inputs or {})
    ]
    (call,) = user_calls
    assert call.turn_index == 1
    assert call.tokens == TokenUsage(50, 20)


def test_every_tool_result_is_a_tool_span():
    events, _ = trace()
    (tool,) = by_type(events, EventType.TOOL_CALL)
    assert tool.tool_name == CALL.name
    assert tool.argument_hash == argument_hash(CALL.arguments)
    assert dict(tool.outputs) == {"content": "user_1"}
    assert tool.outcome is Outcome.SUCCESS
    assert tool.parent_run_id == trace()[0][0].run_id


def test_a_failed_agent_call_is_an_error_span():
    messages = transcript()[:2]
    failed = AgentTurn(
        2, at(2.4), 30.0, SENT, inputs=(messages[1],), error_class="APIError", error_message="503"
    )
    events, feedback = trace(recording=recording(turns=(failed,), messages=messages))
    (call,) = [e for e in by_type(events, EventType.MODEL_CALL) if e.turn_index == 2]
    assert call.outcome is Outcome.ERROR
    assert call.error_class == "APIError"
    assert call.error_message == "503"
    assert call.outputs is None
    assert shape_errors(events, feedback) == []


def test_reward_is_feedback_on_the_root():
    events, feedback = trace()
    assert {f.key for f in feedback} == {"reward", "reward_db"}
    assert all(f.run_id == events[0].run_id for f in feedback)
    assert trace(simulation=simulation(reward=None))[1] == []


def test_ids_are_repeatable_and_never_collide_with_the_export():
    from agent_autonomy_lab.application.export_simulation import simulation_trace
    from agent_autonomy_lab.domain.simulations import SavedRun

    first, second = trace(), trace()
    assert [e.run_id for e in first[0]] == [e.run_id for e in second[0]]
    run = SavedRun("r", "llm_agent", None, None, None, (simulation(),))
    exported, exported_feedback = simulation_trace(
        run, simulation(), ExperimentClass.OFFICIAL_UPSTREAM_BASELINE
    )
    assert not {e.run_id for e in first[0]} & {e.run_id for e in exported}
    assert not {e.event_id for e in first[0]} & {e.event_id for e in exported}
    assert not {f.feedback_id for f in first[1]} & {f.feedback_id for f in exported_feedback}


def test_a_transcript_too_short_for_a_conversation_is_refused():
    short = transcript()[:1]
    with pytest.raises(ValueError, match="cannot form a conversation"):
        trace(recording=recording(turns=(), messages=short))


def test_record_live_run_flushes_once_and_times_both_steps():
    sink = InMemoryTraceSink()
    ticks = iter([0.0, 0.002, 0.502])
    result = record_live_run(recording(), simulation(), sink, timer=lambda: next(ticks))
    assert sink.flush_count == 1
    assert sink.link_errors() == []
    assert result.spans == len(sink.events) == 5
    assert result.feedback == 2
    assert result.root_run_id == sink.roots()[0].run_id
    assert result.thread_id == thread_id_for(SIM_ID)
    assert result.build_ms == pytest.approx(2)
    assert result.flush_ms == pytest.approx(500)


def test_recording_reports_its_shared_fingerprint():
    assert recording().fingerprint == SENT
    assert recording(turns=()).fingerprint is None
