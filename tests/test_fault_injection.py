"""The one-shot tool failure: the wrapper, the trace it produces, and a whole run offline.

Whole runs use real τ-bench retail task 0 with a scripted provider instead of
a model, as in test_deterministic_replay.py.
"""

import json
from uuid import uuid4

import pytest
from langsmith import Client
from tau2.data_model.message import ToolCall
from tau2.environment.environment import Environment
from tau2.registry import registry

from agent_autonomy_lab.adapters.langsmith import LangSmithTraceSink
from agent_autonomy_lab.adapters.litellm import litellm_transport
from agent_autonomy_lab.adapters.tau2 import live_run
from agent_autonomy_lab.adapters.tau2.fault_injection import FAULT_CONTENT, OneShotToolFault
from agent_autonomy_lab.domain.agent_turns import INJECTED_FAULT
from agent_autonomy_lab.domain.events import EventType, Outcome
from agent_autonomy_lab.entrypoints import trace_live_run
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink
from tests.fake_provider import ScriptedProvider, text_reply, tool_reply
from tests.test_langsmith_trace_sink import FakeSession

LOOKUP = "find_user_id_by_name_zip"
ARGS = {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"}


def retail() -> Environment:
    return registry.get_env_constructor("retail")()


def call(name: str = LOOKUP, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=ARGS)


def recovering_outputs() -> list[dict]:
    """The agent's lookup fails once; it tries again and answers."""
    return [
        text_reply("Hi, I want to exchange two items. I'm Yusuf Rossi, zip 19122."),
        tool_reply("call_1", LOOKUP, ARGS),
        tool_reply("call_2", LOOKUP, ARGS),
        text_reply("I found your account. Which order?"),
        text_reply("###STOP###"),
    ]


def test_only_the_first_call_to_the_configured_tool_fails():
    environment = retail()
    fault = OneShotToolFault(LOOKUP)
    fault.install(environment)

    other = environment.get_response(
        ToolCall(id="c0", name="find_user_id_by_email", arguments={"email": "no@example.com"})
    )
    assert fault.injected is None and FAULT_CONTENT not in other.content

    first = environment.get_response(call())
    assert (first.id, first.content, first.error) == ("call_1", FAULT_CONTENT, True)
    assert first.role == "tool" and first.requestor == "assistant"

    later = environment.get_response(call(call_id="call_2"))
    assert later.error is False and later.content == "yusuf_rossi_9620"
    assert fault.injected.call_id == "call_1"
    assert fault.injected.tool_name == LOOKUP
    assert fault.injected.arguments == ARGS
    assert fault.injected.error_class == INJECTED_FAULT


def test_the_agent_is_not_told_the_failure_was_injected():
    assert "inject" not in FAULT_CONTENT.lower() and INJECTED_FAULT not in FAULT_CONTENT


@pytest.mark.parametrize(
    ("tool", "reason"),
    [
        ("exchange_delivered_order_items", "not a read tool"),
        ("calculate", "not a read tool"),
        ("no_such_tool", "not a tool of this environment"),
    ],
)
def test_only_a_read_tool_of_the_environment_can_be_failed(tool, reason):
    with pytest.raises(ValueError, match=reason):
        OneShotToolFault(tool).install(retail())


def test_the_wrapper_touches_one_environment_and_is_never_registered():
    constructor = registry.get_env_constructor("retail")
    wrapped = constructor()
    OneShotToolFault(LOOKUP).install(wrapped)

    assert "get_response" in vars(wrapped)
    assert registry.get_env_constructor("retail") is constructor
    fresh = constructor()
    assert "get_response" not in vars(fresh)
    assert fresh.get_response(call()).error is False


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """A data directory, a fresh agent name, and the trace sink each run sends to."""
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-key")
    monkeypatch.setattr(
        trace_live_run,
        "register_traced_agent",
        lambda: live_run.register_traced_agent(f"test_traced_{uuid4().hex[:8]}"),
    )
    sinks: list[InMemoryTraceSink] = []

    def sink(dry_run, project):
        sinks.append(InMemoryTraceSink())
        return sinks[-1]

    monkeypatch.setattr(trace_live_run, "_sink", sink)
    return tmp_path, sinks


def run(tool: str | None, outputs: list[dict], save_to: str = "fault") -> int:
    args = ["--save-to", save_to, "--dry-run"]
    if tool:
        args += ["--inject-fault", tool]
    with litellm_transport(ScriptedProvider(outputs)):
        return trace_live_run.main(args)


def saved(lab_dir, save_to: str, name: str) -> dict:
    return json.loads((lab_dir / "simulations" / save_to / name).read_text(encoding="utf-8"))


def test_a_run_shows_the_failure_and_the_agents_next_turn(lab):
    lab_dir, sinks = lab
    assert run(LOOKUP, recovering_outputs()) == 0

    (sink,) = sinks
    events = sink.events
    root = next(e for e in events if e.event_type is EventType.CONVERSATION)
    tools = [e for e in events if e.event_type is EventType.TOOL_CALL]
    assert [(e.tool_name, e.outcome, e.error_class) for e in tools] == [
        (LOOKUP, Outcome.ERROR, INJECTED_FAULT),
        (LOOKUP, Outcome.SUCCESS, None),
    ]
    failed = tools[0]
    assert dict(failed.inputs) == ARGS
    assert failed.error_message == FAULT_CONTENT
    assert failed.parent_run_id == root.run_id
    assert failed.context.thread_id == root.context.thread_id
    assert failed.context.simulation_id == root.context.simulation_id

    # The turn that answered the failure received it, and is traced.
    next_turn = next(
        e
        for e in events
        if e.event_type is EventType.MODEL_CALL
        and e.inputs
        and any(m["content"] == FAULT_CONTENT for m in e.inputs["messages"])
    )
    assert next_turn.outcome is Outcome.SUCCESS
    assert next_turn.outputs["messages"][0]["tool_calls"][0]["function"]["name"] == LOOKUP

    summary = saved(lab_dir, "fault", "summary.json")
    assert summary["labels"]["fault_injected"] is True
    assert summary["labels"]["traffic_type"] == "synthetic"
    assert summary["labels"]["experiment_class"] == "custom_lab_experiment"
    assert summary["fault"]["tool_name"] == LOOKUP
    assert summary["fault"]["arguments"] == ARGS
    assert summary["fault"]["tool_run_id"] == str(failed.run_id)
    assert summary["fingerprint"]["match"] is True
    assert all(e.context.fault_injected for e in events)
    assert all(e.context.traffic_type.value == "synthetic" for e in events)
    assert all(e.context.experiment_class.value == "custom_lab_experiment" for e in events)


def test_the_failed_read_changes_neither_the_database_nor_the_reward(lab):
    lab_dir, _ = lab
    outputs = recovering_outputs()
    assert run(LOOKUP, outputs, "fault") == 0
    # The same conversation with no fault and no retry: the control.
    control = [o for o in outputs if o.get("call_id") != "call_1"]
    assert run(None, control, "control") == 0

    fault, plain = (
        saved(lab_dir, "fault", "outcome.json"),
        saved(lab_dir, "control", "outcome.json"),
    )
    assert fault["outcome"]["final_db_hash"] == plain["outcome"]["final_db_hash"]
    assert fault["outcome"]["reward_breakdown"] == plain["outcome"]["reward_breakdown"]
    assert fault["outcome"]["reward"] == plain["outcome"]["reward"]
    assert fault["outcome"]["termination_reason"] == "user_stop"
    assert saved(lab_dir, "control", "summary.json")["labels"]["fault_injected"] is False
    assert saved(lab_dir, "control", "summary.json")["fault"] is None


def test_a_fault_that_never_fired_is_not_traced(lab, capsys):
    lab_dir, sinks = lab
    outputs = [o for o in recovering_outputs() if o.get("call_id") != "call_2"]
    assert run("get_order_details", outputs) == 1
    assert "never called get_order_details" in capsys.readouterr().err
    assert sinks == []
    assert not (lab_dir / "simulations" / "fault" / "summary.json").exists()


def test_a_run_that_raises_after_the_fault_is_still_labeled_and_classified(lab, capsys):
    lab_dir, sinks = lab
    assert run(LOOKUP, recovering_outputs()[:3]) == 1  # the provider has nothing left to say
    capsys.readouterr()

    (sink,) = sinks
    assert all(e.context.fault_injected for e in sink.events)
    assert [e.error_class for e in sink.events if e.event_type is EventType.TOOL_CALL][0] == (
        INJECTED_FAULT
    )
    summary = saved(lab_dir, "fault", "summary.json")
    assert summary["fault"]["tool_name"] == LOOKUP
    assert summary["unfinished"]["error_class"]


def test_a_run_that_raises_before_the_fault_is_not_labeled(lab, capsys):
    _, sinks = lab
    assert run(LOOKUP, recovering_outputs()[:1]) == 1
    capsys.readouterr()

    (sink,) = sinks  # a conversation formed before the provider ran out
    assert not any(e.context.fault_injected for e in sink.events)


def test_a_write_tool_is_refused_before_any_model_call(lab, capsys):
    provider = ScriptedProvider(recovering_outputs())
    args = ["--inject-fault", "cancel_pending_order", "--save-to", "fault", "--dry-run"]
    with litellm_transport(provider):
        assert trace_live_run.main(args) == 1
    assert provider.requests == []
    assert "not a read tool" in capsys.readouterr().err


def test_a_replay_cannot_inject_a_fault(lab, capsys):
    lab_dir, _ = lab
    cassette = lab_dir / "c.json"
    cassette.write_text("{}")
    args = ["--replay", str(cassette), "--inject-fault", LOOKUP, "--save-to", "x", "--dry-run"]
    assert trace_live_run.main(args) == 1
    assert "needs a live run" in capsys.readouterr().err


def test_what_langsmith_receives_for_the_failed_call(lab):
    _, sinks = lab
    assert run(LOOKUP, recovering_outputs()) == 0
    session = FakeSession()
    client = Client(
        api_url="http://langsmith.invalid",
        api_key="fake-key",
        session=session,
        auto_batch_tracing=False,
        info={},
    )
    sink = LangSmithTraceSink(client, "agent-autonomy-lab-test")
    for event in sinks[0].events:
        sink.record(event)
    for item in sinks[0].feedback:
        sink.record_feedback(item)
    sink.flush()

    failed = next(r for r in session.posted if r.get("error"))
    assert failed["name"] == LOOKUP
    assert failed["run_type"] == "tool"
    assert failed["error"] == f"{INJECTED_FAULT}: {FAULT_CONTENT}"
    assert failed["inputs"] == ARGS
    metadata = failed["extra"]["metadata"]
    root = next(r for r in session.posted if not r.get("parent_run_id"))
    assert failed["parent_run_id"] == root["id"]
    assert metadata["error_class"] == INJECTED_FAULT
    assert metadata["tool_name"] == LOOKUP
    assert metadata["fault_injected"] is True
    assert metadata["traffic_type"] == "synthetic"
    assert metadata["experiment_class"] == "custom_lab_experiment"
    assert metadata["thread_id"] == root["extra"]["metadata"]["thread_id"]
    assert root["extra"]["metadata"]["fault_injected"] is True
