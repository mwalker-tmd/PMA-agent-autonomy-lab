import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from tau2.agent.llm_agent import LLMAgent, LLMAgentState, create_llm_agent
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)

from agent_autonomy_lab.adapters.tau2.live_run import trial_seed
from agent_autonomy_lab.adapters.tau2.traced_agent import (
    TracedAgent,
    TracedAgentFactory,
    TurnRecorder,
    fingerprint,
)
from agent_autonomy_lab.domain.simulations import Role

T0 = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class StubInner:
    """Stands in for ``LLMAgent``: scripted replies, no model."""

    def __init__(self, replies, delay: float = 0.0) -> None:
        self.tools = [
            SimpleNamespace(openai_schema={"type": "function", "function": {"name": "t"}})
        ]
        self.domain_policy = "policy"
        self.llm = "model-x"
        self.llm_args = {"reasoning_effort": "medium"}
        self.additional_stop_tool_names = frozenset({"end_call"})
        self.replies = list(replies)
        self.delay = delay
        self.calls = []
        self.stopped = None

    def set_seed(self, seed):
        self.llm_args["seed"] = seed

    def is_stop(self, message):
        return message.content == "STOP"

    def get_init_state(self, message_history=None):
        return LLMAgentState(
            system_messages=[SystemMessage(role="system", content="system prompt")],
            messages=list(message_history or []),
        )

    def generate_next_message(self, message, state):
        self.calls.append(message)
        time.sleep(self.delay)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        state.messages.append(reply)
        return reply, state

    def stop(self, message=None, state=None):
        self.stopped = message


def opener():
    return AssistantMessage(role="assistant", content="Hi! How can I help you today?")


def tool_reply():
    return AssistantMessage(
        role="assistant",
        tool_calls=[ToolCall(id="call-1", name="find_user", arguments={"zip": "19122"})],
        usage={"prompt_tokens": 100, "completion_tokens": 10},
        cost=0.001,
        generation_time_seconds=1.5,
    )


def text_reply(content="Done."):
    return AssistantMessage(
        role="assistant",
        content=content,
        usage={"prompt_tokens": 120, "completion_tokens": 5},
        cost=0.002,
    )


def user(content="Please help.", **fields):
    return UserMessage(role="user", content=content, **fields)


def tool_result(content="user_1"):
    return ToolMessage(id="call-1", role="tool", content=content, requestor="assistant")


def run_conversation(agent: TracedAgent):
    state = agent.get_init_state([opener()])
    reply, state = agent.generate_next_message(user(), state)
    reply2, state = agent.generate_next_message(tool_result(), state)
    agent.stop(user("Thanks ###STOP###"), state)
    return reply, reply2, state


def test_calls_pass_through_unchanged():
    replies = [tool_reply(), text_reply()]
    inner = StubInner(list(replies))
    agent = TracedAgent(inner, TurnRecorder("lab_agent", FakeClock()))
    incoming = user()
    state = agent.get_init_state([opener()])
    reply, returned_state = agent.generate_next_message(incoming, state)
    assert reply is replies[0]
    assert returned_state is state
    assert inner.calls == [incoming]
    agent.set_seed(7)
    assert inner.llm_args["seed"] == 7
    assert agent.is_stop(text_reply("STOP")) and not agent.is_stop(text_reply())
    assert agent.additional_stop_tool_names == frozenset({"end_call"})
    assert agent.tools is inner.tools and agent.domain_policy == inner.domain_policy
    last = user("bye")
    agent.stop(last, state)
    assert inner.stopped is last


def test_records_the_transcript_in_order_with_positions():
    agent = TracedAgent(StubInner([tool_reply(), text_reply()]), TurnRecorder("lab", FakeClock()))
    run_conversation(agent)
    recording = agent.recorder.recording()
    roles = [m.role for m in recording.transcript]
    assert roles == [
        Role.ASSISTANT,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.USER,
    ]
    assert [m.turn_index for m in recording.transcript] == list(range(6))
    assert recording.transcript[-1].content == "Thanks ###STOP###"
    assert recording.transcript[3].tool_call_id == "call-1"
    assert recording.agent_version == "lab"
    assert recording.started_at < recording.ended_at


def test_records_each_turn_with_inputs_output_and_usage():
    agent = TracedAgent(StubInner([tool_reply(), text_reply()]), TurnRecorder("lab", FakeClock()))
    run_conversation(agent)
    first, second = agent.recorder.recording().turns
    assert [m.role for m in first.inputs] == [Role.USER]
    assert first.turn_index == 2
    assert first.output.tool_calls[0].name == "find_user"
    assert first.output.tokens.prompt_tokens == 100
    assert first.output.cost == 0.001
    assert [m.role for m in second.inputs] == [Role.TOOL]
    assert second.turn_index == 4
    assert first.started_at.tzinfo is not None
    assert first.elapsed_ms >= 0


def test_tool_results_in_one_message_are_separate_inputs():
    reply = AssistantMessage(
        role="assistant",
        tool_calls=[
            ToolCall(id="a", name="t1", arguments={}),
            ToolCall(id="b", name="t2", arguments={}),
        ],
    )
    agent = TracedAgent(StubInner([reply, text_reply()]), TurnRecorder("lab", FakeClock()))
    state = agent.get_init_state([opener()])
    _, state = agent.generate_next_message(user(), state)
    results = [
        ToolMessage(id=i, role="tool", content="ok", requestor="assistant") for i in ("a", "b")
    ]
    agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=results), state)
    turn = agent.recorder.recording().turns[1]
    assert [m.tool_call_id for m in turn.inputs] == ["a", "b"]
    assert [m.turn_index for m in turn.inputs] == [3, 4]
    assert turn.turn_index == 5


def test_every_path_gives_each_message_its_own_position():
    agent = TracedAgent(StubInner([text_reply()]), TurnRecorder("lab", FakeClock()))
    state = agent.get_init_state([opener(), user("earlier"), text_reply("earlier reply")])
    agent.generate_next_message(user(), state)
    agent.stop(MultiToolMessage(role="tool", tool_messages=[tool_result(), tool_result()]), state)
    transcript = agent.recorder.recording().transcript
    assert [m.turn_index for m in transcript] == list(range(len(transcript))) == list(range(7))


def test_a_failed_call_is_recorded_and_reraised():
    error = RuntimeError("provider unavailable")
    agent = TracedAgent(StubInner([error]), TurnRecorder("lab", FakeClock()))
    state = agent.get_init_state([opener()])
    with pytest.raises(RuntimeError, match="provider unavailable"):
        agent.generate_next_message(user(), state)
    (turn,) = agent.recorder.recording().turns
    assert turn.output is None
    assert turn.error_class == "RuntimeError"
    assert turn.error_message == "provider unavailable"
    assert turn.turn_index == 2


def test_each_turn_records_what_was_sent():
    inner = StubInner([tool_reply(), text_reply()])
    agent = TracedAgent(inner, TurnRecorder("lab", FakeClock()))
    agent.set_seed(42)
    state = agent.get_init_state([opener()])
    agent.generate_next_message(user(), state)
    inner.llm_args["reasoning_effort"] = "high"
    agent.generate_next_message(tool_result(), state)
    first, second = agent.recorder.recording().turns
    assert first.fingerprint.model_args == {"reasoning_effort": "medium", "seed": 42}
    assert first.fingerprint.differences(second.fingerprint) == ["model_args_version"]
    assert agent.recorder.recording().fingerprint is None


def test_overhead_excludes_the_agent_call():
    agent = TracedAgent(StubInner([text_reply()], delay=0.05), TurnRecorder("lab", FakeClock()))
    state = agent.get_init_state([opener()])
    agent.generate_next_message(user(), state)
    recording = agent.recorder.recording()
    assert recording.turns[0].elapsed_ms >= 50
    assert 0 < recording.overhead_ms < 50


def test_stop_is_recorded_once():
    agent = TracedAgent(StubInner([text_reply()]), TurnRecorder("lab", FakeClock()))
    state = agent.get_init_state([opener()])
    agent.generate_next_message(user(), state)
    agent.stop(user("bye"), state)
    ended = agent.recorder.ended_at
    agent.stop(None, state)
    assert agent.recorder.ended_at == ended
    assert len(agent.recorder.recording().transcript) == 4


def test_local_timestamps_are_read_in_this_machines_zone():
    agent = TracedAgent(StubInner([text_reply()]), TurnRecorder("lab", FakeClock()))
    # Today, because the agent reads every time in the machine's current UTC offset.
    local = datetime.now().replace(microsecond=0)
    state = agent.get_init_state([opener()])
    agent.generate_next_message(user(timestamp=local.isoformat()), state)
    (_, message, _) = agent.recorder.recording().transcript
    assert message.timestamp == local.astimezone(UTC)


def test_factory_wraps_upstreams_llm_agent_unchanged():
    factory = TracedAgentFactory("lab")
    kwargs = {"llm": "model-x", "llm_args": {"reasoning_effort": "medium"}, "task": object()}
    agent = factory([], "policy text", **kwargs)
    reference = create_llm_agent([], "policy text", **kwargs)
    assert isinstance(agent.inner, LLMAgent)
    assert type(agent.inner) is type(reference)
    agent.set_seed(5)
    reference.set_seed(5)
    ours = fingerprint(agent.inner, agent.get_init_state().system_messages)
    theirs = fingerprint(reference, reference.get_init_state().system_messages)
    assert ours == theirs
    assert ours.model_args == theirs.model_args
    assert factory.recorders == [agent.recorder]


def test_fingerprint_changes_with_the_prompt_tools_or_args():
    base = LLMAgent(tools=[], domain_policy="policy", llm="m", llm_args={"a": 1})
    prompt = base.get_init_state().system_messages
    other_policy = LLMAgent(tools=[], domain_policy="policy 2", llm="m", llm_args={"a": 1})
    other_args = LLMAgent(tools=[], domain_policy="policy", llm="m", llm_args={"a": 2})
    sent = fingerprint(base, prompt)
    assert fingerprint(other_policy, other_policy.get_init_state().system_messages).differences(
        sent
    ) == ["prompt_version"]
    assert fingerprint(other_args, prompt).differences(sent) == ["model_args_version"]


def test_trial_seed_matches_tau2_run():
    # The Milestone 2 baseline (run seed 42) recorded seed 670487 for trial 0.
    assert trial_seed(42) == 670487
