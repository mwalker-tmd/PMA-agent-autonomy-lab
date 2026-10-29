import json

import pytest
from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import AssistantMessage, UserMessage

from agent_autonomy_lab.adapters.tau2 import live_run
from agent_autonomy_lab.adapters.tau2.live_run import (
    LiveRunFailed,
    LiveRunSettings,
    run_traced_task,
)
from agent_autonomy_lab.adapters.tau2.traced_agent import TracedAgentFactory, fingerprint
from agent_autonomy_lab.application.trace_live_run import live_trace
from agent_autonomy_lab.domain.agent_turns import UnfinishedSimulation
from agent_autonomy_lab.domain.events import EventType, Outcome, TrafficType
from agent_autonomy_lab.entrypoints import trace_live_run as entrypoint
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink

ARGS = {"reasoning_effort": "medium"}
SETTINGS = LiveRunSettings(
    task_id="0", agent_llm="model-x", user_llm="model-u", agent_llm_args=ARGS
)


class ProviderError(Exception):
    pass


def reference_agent(seed: int) -> LLMAgent:
    agent = LLMAgent(tools=[], domain_policy="policy", llm="model-x", llm_args=ARGS)
    agent.set_seed(seed)
    return agent


@pytest.fixture
def factory(monkeypatch):
    """A fresh factory, and a τ-bench runner whose agent's model call fails on the second turn."""
    factory = TracedAgentFactory("lab_traced_llm_agent")

    def reference(settings, task, seed):
        agent = reference_agent(seed)
        return fingerprint(agent, agent.get_init_state().system_messages), "policy-test"

    def run_single_task(config, task, seed):
        agent = factory([], "policy", llm=config.llm_agent, llm_args=config.llm_args_agent)
        replies = iter([AssistantMessage(role="assistant", content="Which order?")])

        def model_call(message, state):
            reply = next(replies, None)
            if reply is None:
                raise ProviderError("503 from provider")
            state.messages += [message, reply]
            return reply, state

        agent.inner.generate_next_message = model_call
        agent.set_seed(seed)
        state = agent.get_init_state([AssistantMessage(role="assistant", content="Hi!")])
        _, state = agent.generate_next_message(UserMessage(role="user", content="Help"), state)
        agent.generate_next_message(UserMessage(role="user", content="#W1"), state)

    monkeypatch.setattr(live_run, "get_tasks", lambda domain, task_ids: [object()])
    monkeypatch.setattr(live_run, "reference", reference)
    monkeypatch.setattr(live_run, "run_single_task", run_single_task)
    monkeypatch.setattr(entrypoint, "register_traced_agent", lambda: factory)
    return factory


def test_a_raising_run_carries_its_partial_recording(factory):
    with pytest.raises(LiveRunFailed) as raised:
        run_traced_task(SETTINGS, factory)
    failure = raised.value.failure
    assert isinstance(failure.cause, ProviderError)
    assert raised.value.__cause__ is failure.cause
    assert failure.task_id == "0"
    assert failure.trial_seed == 670487
    assert failure.policy_version == "policy-test"
    recording = failure.recording
    assert [m.content for m in recording.transcript] == ["Hi!", "Help", "Which order?", "#W1"]
    ok, failed = recording.turns
    assert ok.output.content == "Which order?"
    assert failed.error_class == "ProviderError"
    assert failed.fingerprint == failure.reference


def test_an_unfinished_run_is_traced_with_an_error_root_and_no_reward(factory):
    with pytest.raises(LiveRunFailed) as raised:
        run_traced_task(SETTINGS, factory)
    unfinished = UnfinishedSimulation("unfinished-1", "0", "ProviderError", "503 from provider")
    events, feedback = live_trace(raised.value.failure.recording, unfinished)
    root = events[0]
    assert root.context.simulation_id == "unfinished-1"
    assert root.outcome is Outcome.ERROR
    assert root.error_class == "ProviderError"
    assert feedback == []
    errors = [e for e in events if e.event_type is EventType.MODEL_CALL and e.error_class]
    assert [e.turn_index for e in errors] == [4]


def test_unfinished_ids_cannot_pass_for_benchmark_ids():
    with pytest.raises(ValueError, match="unfinished-"):
        UnfinishedSimulation("a428164f-fdfc-4245-b30b-f871b8d62e7f", "0", "ProviderError")


def test_the_entrypoint_traces_the_partial_thread_and_exits_1(
    factory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    sinks = []
    monkeypatch.setattr(
        entrypoint, "_sink", lambda dry_run, project: sinks.append(InMemoryTraceSink()) or sinks[-1]
    )
    assert entrypoint.main(["--dry-run", "--save-to", "failed", "--agent-llm", "model-x"]) == 1
    captured = capsys.readouterr()
    assert "ProviderError: 503 from provider" in captured.err
    assert "partial thread was traced" in captured.err

    (sink,) = sinks
    (root,) = sink.roots()
    assert sink.link_errors() == []
    assert root.outcome is Outcome.ERROR
    assert root.context.simulation_id.startswith("unfinished-")
    assert root.context.traffic_type is TrafficType.SYNTHETIC
    assert sink.feedback == []

    summary = json.loads((tmp_path / "simulations" / "failed" / "summary.json").read_text())
    assert summary["simulation_id"] == root.context.simulation_id
    assert summary["unfinished"] == {
        "error_class": "ProviderError",
        "error_message": "503 from provider",
    }
    assert summary["fingerprint"]["match"] is True
    assert not (tmp_path / "simulations" / "failed" / "simulation.json").exists()
