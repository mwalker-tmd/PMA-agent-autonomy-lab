"""LangSmith adapter tests.

They run the real ``langsmith.Client`` with a fake HTTP session, so the
payloads checked are the ones the SDK would send, and no test needs network
access.
"""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
import requests
from langsmith import Client

from agent_autonomy_lab.adapters.langsmith import (
    LangSmithTraceSink,
    StaleTraceError,
    TraceShapeError,
)
from agent_autonomy_lab.adapters.langsmith.trace_sink import message_errors
from agent_autonomy_lab.adapters.tau2 import read_results
from agent_autonomy_lab.application.export_simulation import export_run
from agent_autonomy_lab.domain.events import (
    EventType,
    ExperimentClass,
    Feedback,
    GateEventClass,
    Outcome,
    TokenUsage,
    argument_hash,
    new_id,
    parent_first,
)
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink
from tests.conftest import DENVER, NOT_EXPORTED, SAMPLE_RUN, T0, make_context, make_event

PROJECT = "agent-autonomy-lab-test"
NOT_METADATA = ("inputs", "outputs", "run_id", "parent_run_id")

OPENER = {"role": "assistant", "content": "Hi! How can I help you today?"}
ASK = {"role": "user", "content": "Where is order #W2378156?"}
CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_order_details",
                "arguments": json.dumps({"order_id": "#W2378156"}),
            },
        }
    ],
}
RESULT = {"role": "tool", "tool_call_id": "call_1", "content": "Error: Order not found"}
REPLY = {"role": "assistant", "content": "I could not find that order. Can you check the ID?"}


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
NOW = T0 + timedelta(minutes=5)  # the sink's clock: shortly after the scenario


class FakeSession(requests.Session):
    """Records runs and feedback; knows one project; answers everything else with ``{}``."""

    def __init__(self) -> None:
        super().__init__()
        self.posted: list[dict] = []
        self.feedback: list[dict] = []

    def request(self, method, url, **kwargs):
        body = {}
        if method == "POST" and url.endswith("/runs"):
            self.posted.append(json.loads(kwargs["data"]))
        elif method == "POST" and url.endswith("/feedback"):
            self.feedback.append(json.loads(kwargs["data"]))
        elif method == "GET" and url.endswith("/sessions"):
            tenant = "22222222-2222-4222-8222-222222222222"
            body = [
                {
                    "id": PROJECT_ID,
                    "name": PROJECT,
                    "tenant_id": tenant,
                    "reference_dataset_id": None,
                }
            ]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response.url = url
        return response


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def sink(session) -> LangSmithTraceSink:
    client = Client(
        api_url="http://langsmith.invalid",
        api_key="fake-key",
        session=session,
        auto_batch_tracing=False,
        info={},
    )
    return LangSmithTraceSink(client, PROJECT, clock=lambda: NOW)


def scenario_events(context, *, fail_tool: bool = True):
    """One conversation with a model call, a gated tool call, and a reply.

    Events are in finish order, as a live run records them: children first,
    the root last. Returns ``(events, conversation, model, tool, gates)``.
    """
    root = new_id()
    tool_run = new_id()
    model = make_event(
        EventType.MODEL_CALL,
        context,
        timestamp=T0 + timedelta(seconds=1),
        parent_run_id=root,
        turn_index=2,
        elapsed_ms=850.0,
        tokens=TokenUsage(prompt_tokens=1200, completion_tokens=80),
        cost=0.0012,
        inputs={"messages": [OPENER, ASK]},
        outputs={"messages": [CALL]},
    )
    attempted = make_event(
        EventType.GATE,
        context,
        timestamp=T0 + timedelta(seconds=2),
        run_id=tool_run,
        gate_event=GateEventClass.ATTEMPTED,
    )
    error = {"outcome": Outcome.ERROR, "error_class": "injected_fault"} if fail_tool else {}
    tool = make_event(
        EventType.TOOL_CALL,
        context,
        timestamp=T0 + timedelta(seconds=2),
        run_id=tool_run,
        parent_run_id=root,
        turn_index=3,
        argument_hash=argument_hash({"order_id": "#W2378156"}),
        inputs={"order_id": "#W2378156"},
        outputs={"content": RESULT["content"]},
        **(error | ({"error_message": RESULT["content"]} if fail_tool else {})),
    )
    executed = make_event(
        EventType.GATE,
        context,
        timestamp=T0 + timedelta(seconds=2),
        run_id=tool_run,
        gate_event=GateEventClass.EXECUTED,
    )
    conversation = make_event(
        EventType.CONVERSATION,
        context,
        run_id=root,
        elapsed_ms=5000.0,
        inputs={"messages": [OPENER, ASK]},
        outputs={"messages": [CALL, RESULT, REPLY]},
    )
    events = [model, attempted, tool, executed, conversation]
    return events, conversation, model, tool, (attempted, executed)


def record_scenario(sink, context, *, fail_tool: bool = True):
    events, *parts = scenario_events(context, fail_tool=fail_tool)
    for event in events:
        sink.record(event)
    return parts


def runs_by_id(session) -> dict[str, dict]:
    return {run["id"]: run for run in session.posted}


def test_conversation_root_has_top_level_messages(sink, session, context):
    conversation, *_ = record_scenario(sink, context)
    sink.flush()
    root = runs_by_id(session)[str(conversation.run_id)]
    assert root["run_type"] == "chain"
    assert root["name"] == "conversation"
    assert root.get("parent_run_id") is None
    assert root["inputs"] == {"messages": [OPENER, ASK]}
    assert root["outputs"] == {"messages": [CALL, RESULT, REPLY]}
    assert root["session_name"] == PROJECT


def test_child_runs_nest_under_the_conversation(sink, session, context):
    conversation, model, tool, _ = record_scenario(sink, context)
    sink.flush()
    runs = runs_by_id(session)
    root_id = str(conversation.run_id)
    llm, tool_run = runs[str(model.run_id)], runs[str(tool.run_id)]
    assert (llm["run_type"], llm["parent_run_id"], llm["trace_id"]) == ("llm", root_id, root_id)
    assert (tool_run["run_type"], tool_run["name"]) == ("tool", "get_order_details")
    assert (tool_run["parent_run_id"], tool_run["trace_id"]) == (root_id, root_id)
    root_segment = runs[root_id]["dotted_order"]
    assert root_segment == f"20261027T062509000000Z{root_id}"
    assert llm["dotted_order"] == f"{root_segment}.20261027T062510000000Z{model.run_id}"


def test_roots_are_sent_before_their_children(sink, session, context):
    conversation, model, tool, _ = record_scenario(sink, context)
    sink.flush()
    assert [run["id"] for run in session.posted] == [
        str(conversation.run_id), str(model.run_id), str(tool.run_id),
    ]  # fmt: skip


def test_runs_are_threaded_by_thread_metadata(sink, session):
    first = make_context("sim-a")
    second = make_context("sim-b")
    record_scenario(sink, first)
    record_scenario(sink, second)
    sink.flush()
    threads = {run["extra"]["metadata"]["thread_id"] for run in session.posted}
    assert threads == {first.thread_id, second.thread_id}
    for run in session.posted:
        metadata = run["extra"]["metadata"]
        expected = first if metadata["simulation_id"] == "sim-a" else second
        assert metadata["thread_id"] == expected.thread_id


def test_metadata_carries_the_identifier_contract(sink, session, context):
    _, model, tool, _ = record_scenario(sink, context)
    sink.flush()
    runs = runs_by_id(session)
    metadata = runs[str(tool.run_id)]["extra"]["metadata"]
    assert metadata["event_id"] == str(tool.event_id)
    assert metadata["schema_version"] == "trace-event/1"
    assert metadata["simulation_id"] == context.simulation_id
    assert metadata["task_id"] == context.task_id
    assert metadata["turn_index"] == 3
    assert metadata["autonomy_level"] == "n/a"
    assert metadata["traffic_type"] == "synthetic"
    assert metadata["experiment_class"] == "custom_lab_experiment"
    assert metadata["argument_hash"] == tool.argument_hash
    assert "inputs" not in metadata and "outputs" not in metadata
    llm = runs[str(model.run_id)]
    assert llm["outputs"]["usage_metadata"] == {
        "input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280, "total_cost": 0.0012,
    }  # fmt: skip
    assert llm["end_time"] == "2026-10-27T06:25:10.850000+00:00"


def test_tool_error_is_an_error_run_with_its_class(sink, session, context):
    _, _, tool, _ = record_scenario(sink, context)
    sink.flush()
    run = runs_by_id(session)[str(tool.run_id)]
    assert run["error"] == "injected_fault: Error: Order not found"
    assert run["extra"]["metadata"]["error_class"] == "injected_fault"
    assert run["extra"]["metadata"]["outcome"] == "error"
    assert run["outputs"] == {"content": "Error: Order not found"}


def test_successful_tool_run_has_no_error(sink, session, context):
    _, _, tool, _ = record_scenario(sink, context, fail_tool=False)
    sink.flush()
    assert runs_by_id(session)[str(tool.run_id)].get("error") is None


def test_gate_events_are_attached_to_their_tool_run(sink, session, context):
    _, _, tool, (attempted, executed) = record_scenario(sink, context)
    sink.flush()
    events = runs_by_id(session)[str(tool.run_id)]["events"]
    assert [e["name"] for e in events] == ["gate:ATTEMPTED", "gate:EXECUTED"]
    assert [e["kwargs"]["event_id"] for e in events] == [
        str(attempted.event_id), str(executed.event_id),
    ]  # fmt: skip


def test_same_scenario_gives_the_same_events_in_memory_and_in_langsmith(sink, session, context):
    memory = InMemoryTraceSink()
    events, *_ = scenario_events(context)
    for target in (memory, sink):
        for event in events:
            target.record(event)
    sink.flush()
    assert memory.link_errors() == []

    spans = parent_first(memory.events)
    assert [run["id"] for run in session.posted] == [str(span.run_id) for span in spans]
    for span, run in zip(spans, session.posted, strict=True):
        record = span.to_dict()
        metadata = dict(run["extra"]["metadata"])
        metadata.pop("revision_id", None)  # added by the SDK: the lab's git version
        assert metadata == {
            key: value
            for key, value in record.items()
            if value is not None and key not in NOT_METADATA
        }
        assert run.get("parent_run_id") == record["parent_run_id"]
        assert run["inputs"] == (record["inputs"] or {})
        outputs = {k: v for k, v in run["outputs"].items() if k != "usage_metadata"}
        assert outputs == (record["outputs"] or {})
        gates = [str(gate.event_id) for gate in memory.gate_events(span.run_id)]
        assert [e["kwargs"]["event_id"] for e in run.get("events", [])] == gates


def test_flush_clears_the_buffer(sink, session, context):
    record_scenario(sink, context)
    sink.flush()
    sink.flush()
    assert len(session.posted) == 3


def test_broken_trace_is_rejected_whole_and_kept(sink, session, context):
    orphan = make_event(EventType.MODEL_CALL, context)
    sink.record(orphan)
    with pytest.raises(TraceShapeError) as raised:
        sink.flush()
    assert raised.value.errors == [
        "simulation sim-00023: 0 conversation roots, expected 1",
        f"{orphan.event_id}: parent {orphan.parent_run_id} not recorded",
    ]
    assert session.posted == []
    with pytest.raises(TraceShapeError):
        sink.flush()


def test_adapter_enforces_whole_run_context_links(sink, session, context):
    root = make_event(
        EventType.CONVERSATION,
        context,
        inputs={"messages": [OPENER]},
        outputs={"messages": [REPLY]},
    )
    child = make_event(
        EventType.MODEL_CALL, replace(context, model_id="gpt-6-luna"), parent_run_id=root.run_id
    )
    sink.record(child)
    sink.record(root)
    with pytest.raises(TraceShapeError, match="parent has a different run context"):
        sink.flush()
    assert session.posted == []


def test_adapter_enforces_one_root_per_simulation(sink, session, context):
    for _ in range(2):
        sink.record(
            make_event(
                EventType.CONVERSATION,
                context,
                inputs={"messages": [OPENER]},
                outputs={"messages": [REPLY]},
            )
        )
    with pytest.raises(TraceShapeError, match="2 conversation roots, expected 1"):
        sink.flush()
    assert session.posted == []


def test_duplicate_event_is_rejected_at_flush(sink, session, context):
    root = make_event(
        EventType.CONVERSATION,
        context,
        inputs={"messages": [OPENER]},
        outputs={"messages": [REPLY]},
    )
    sink.record(root)
    sink.record(root)
    with pytest.raises(TraceShapeError, match="duplicate event_id"):
        sink.flush()


@pytest.mark.parametrize(
    "inputs, outputs, problem",
    [
        (None, {"messages": [REPLY]}, "inputs need a non-empty top-level messages list"),
        ({"messages": []}, {"messages": [REPLY]}, "inputs need a non-empty"),
        ({"messages": [OPENER]}, {"text": "hi"}, "outputs need a non-empty"),
        ({"messages": [{"role": "bot", "content": "hi"}]}, {"messages": [REPLY]}, "role must be"),
    ],
)
def test_conversation_without_messages_is_rejected(
    sink, session, context, inputs, outputs, problem
):
    sink.record(make_event(EventType.CONVERSATION, context, inputs=inputs, outputs=outputs))
    with pytest.raises(TraceShapeError, match=problem):
        sink.flush()
    assert session.posted == []


@pytest.mark.parametrize("message", [OPENER, ASK, CALL, RESULT, REPLY])
def test_well_formed_messages_pass(message):
    assert message_errors(message) == []


@pytest.mark.parametrize(
    "message, problem",
    [
        ("hello", "message must be an object"),
        ({"content": "hi"}, "role must be one of"),
        ({"role": {}, "content": "hi"}, "role must be one of"),
        ({"role": ["user"], "content": "hi"}, "role must be one of"),
        ({"role": "user", "content": ["hi"]}, "content must be a string or null"),
        ({"role": "user", "content": None}, "a user message needs content"),
        ({"role": "assistant", "content": None}, "needs content or tool_calls"),
        ({"role": "tool", "content": "ok"}, "a tool message needs a tool_call_id"),
        ({"role": "user", "content": "hi", "tool_calls": []}, "only assistant messages"),
        (
            {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "f"}}]},
            "tool_calls[0]: function arguments must be a JSON string",
        ),
        (
            {"role": "assistant", "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]},
            "tool_calls[0]: needs an id",
        ),
    ],
)
def test_malformed_messages_are_described(message, problem):
    assert any(problem in error for error in message_errors(message))


@pytest.mark.parametrize(
    "fields, usage",
    [
        ({"cost": 0.0012}, {"total_cost": 0.0012}),
        (
            {"tokens": TokenUsage(prompt_tokens=10, completion_tokens=2)},
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        ),
        ({}, None),
    ],
)
def test_model_call_usage_is_exported_when_known(sink, session, context, fields, usage):
    root = make_event(
        EventType.CONVERSATION,
        context,
        inputs={"messages": [OPENER]},
        outputs={"messages": [REPLY]},
    )
    model = make_event(EventType.MODEL_CALL, context, parent_run_id=root.run_id, **fields)
    sink.record(model)
    sink.record(root)
    sink.flush()
    assert runs_by_id(session)[str(model.run_id)]["outputs"].get("usage_metadata") == usage


def test_object_valued_role_is_a_trace_shape_error(sink, session, context):
    bad = {"role": {}, "content": "hi"}
    sink.record(
        make_event(
            EventType.CONVERSATION,
            context,
            inputs={"messages": [bad]},
            outputs={"messages": [REPLY]},
        )
    )
    with pytest.raises(TraceShapeError, match="role must be one of"):
        sink.flush()
    assert session.posted == []


def reward_feedback(conversation, **overrides):
    fields = {
        "feedback_id": new_id(),
        "run_id": conversation.run_id,
        "context": conversation.context,
        "key": "reward",
        "score": 1.0,
        "comment": "τ-bench reward breakdown: DB 1",
        "value": {"reward_breakdown": {"DB": 1.0}},
    }
    return Feedback(**(fields | overrides))


def test_feedback_is_sent_to_its_run_after_the_runs(sink, session, context):
    conversation, *_ = record_scenario(sink, context)
    item = reward_feedback(conversation)
    sink.record_feedback(item)
    sink.flush()
    (sent,) = session.feedback
    assert sent["id"] == str(item.feedback_id)
    assert sent["run_id"] == sent["trace_id"] == str(conversation.run_id)
    assert sent["session_id"] == PROJECT_ID
    assert (sent["key"], sent["score"]) == ("reward", 1.0)
    assert sent["value"] == {"reward_breakdown": {"DB": 1.0}}
    assert sent["comment"] == "τ-bench reward breakdown: DB 1"
    source = sent["feedback_source"]["metadata"]
    assert source["simulation_id"] == context.simulation_id
    assert source["schema_version"] == "trace-event/1"


def test_feedback_on_a_child_run_carries_the_root_trace_id(sink, session, context):
    conversation, _, tool, _ = record_scenario(sink, context)
    sink.record_feedback(reward_feedback(conversation, run_id=tool.run_id, key="tool_ok"))
    sink.flush()
    (sent,) = session.feedback
    assert (sent["run_id"], sent["trace_id"]) == (str(tool.run_id), str(conversation.run_id))


def test_feedback_on_an_unrecorded_run_rejects_the_whole_flush(sink, session, context):
    conversation, *_ = record_scenario(sink, context)
    item = reward_feedback(conversation, run_id=new_id())
    sink.record_feedback(item)
    with pytest.raises(TraceShapeError, match="is not a recorded span"):
        sink.flush()
    assert session.posted == [] and session.feedback == []


def test_exported_saved_run_reaches_langsmith_without_unexported_content(sink, session):
    run = read_results(SAMPLE_RUN, DENVER)
    (exported,) = export_run(run, sink, ExperimentClass.OFFICIAL_UPSTREAM_BASELINE)
    assert len(session.posted) == exported.spans == 6
    assert len(session.feedback) == exported.feedback == 3
    root = session.posted[0]
    assert root["extra"]["metadata"]["simulation_id"] == exported.simulation_id
    assert root["extra"]["metadata"]["thread_id"] == exported.thread_id
    payload = json.dumps(session.posted) + json.dumps(session.feedback)
    for marker in NOT_EXPORTED:
        assert marker not in payload
    assert "reward" not in json.dumps(root["inputs"]) + json.dumps(root["outputs"])


def test_exporting_a_saved_run_again_sends_the_same_ids(sink, session):
    run = read_results(SAMPLE_RUN, DENVER)
    for _ in range(2):
        export_run(run, sink, ExperimentClass.OFFICIAL_UPSTREAM_BASELINE)
    first, second = session.posted[:6], session.posted[6:]
    assert [r["id"] for r in first] == [r["id"] for r in second]
    assert [f["id"] for f in session.feedback[:3]] == [f["id"] for f in session.feedback[3:]]


def old_sink(session, now, stored=None, lookups=None):
    """A sink whose clock is ``now`` and whose LangSmith holds ``stored`` runs."""
    client = Client(
        api_url="http://langsmith.invalid",
        api_key="fake-key",
        session=session,
        auto_batch_tracing=False,
        info={},
    )

    def stored_runs(trace_id):
        if lookups is not None:
            lookups.append(trace_id)
        return stored or {}

    return LangSmithTraceSink(client, PROJECT, clock=lambda: now, stored_runs=stored_runs)


def test_trace_older_than_langsmith_accepts_is_shifted_whole(session, context):
    later = T0 + timedelta(hours=49)
    sink = old_sink(session, later)
    conversation, model, tool, _ = record_scenario(sink, context)
    sink.flush()
    runs = runs_by_id(session)
    root, llm = runs[str(conversation.run_id)], runs[str(model.run_id)]
    assert root["start_time"] == later.isoformat()
    assert llm["start_time"] == (later + timedelta(seconds=1)).isoformat()
    assert llm["end_time"] == (later + timedelta(seconds=1.85)).isoformat()
    assert llm["dotted_order"].startswith(f"{later:%Y%m%dT%H%M%S%fZ}{conversation.run_id}.")
    assert runs[str(tool.run_id)]["events"][0]["time"] == (later + timedelta(seconds=2)).isoformat()
    assert root["extra"]["metadata"]["timestamp"] == T0.isoformat()
    assert llm["extra"]["metadata"]["timestamp"] == (T0 + timedelta(seconds=1)).isoformat()


def test_retry_after_a_partial_upload_reuses_the_stored_root_time(session, context):
    first_export = T0 + timedelta(hours=49)
    events, conversation, model, tool, _ = scenario_events(context)
    stored = {conversation.run_id: first_export, model.run_id: first_export}
    sink = old_sink(session, first_export + timedelta(hours=3), stored)
    for event in events:
        sink.record(event)
    sink.flush()
    assert [run["id"] for run in session.posted] == [str(tool.run_id)]
    (sent,) = session.posted
    assert sent["start_time"] == (first_export + timedelta(seconds=2)).isoformat()
    assert sent["dotted_order"].startswith(f"{first_export:%Y%m%dT%H%M%S%fZ}{conversation.run_id}.")


def test_missing_runs_anchored_to_a_day_old_root_are_refused(session, context):
    first_export = T0 + timedelta(hours=49)
    events, conversation, model, tool, _ = scenario_events(context)
    sink = old_sink(
        session, first_export + timedelta(hours=30), {conversation.run_id: first_export}
    )
    for event in events:
        sink.record(event)
    sink.record_feedback(reward_feedback(conversation))
    with pytest.raises(StaleTraceError) as raised:
        sink.flush()
    assert set(raised.value.run_ids) == {model.run_id, tool.run_id}
    assert session.posted == [] and session.feedback == []


def test_fully_stored_old_trace_sends_no_runs(session, context):
    first_export = T0 + timedelta(hours=49)
    events, conversation, model, tool, _ = scenario_events(context)
    stored = {run_id: first_export for run_id in (conversation.run_id, model.run_id, tool.run_id)}
    sink = old_sink(session, first_export + timedelta(hours=30), stored)
    for event in events:
        sink.record(event)
    sink.record_feedback(reward_feedback(conversation))
    sink.flush()
    assert session.posted == []
    assert len(session.feedback) == 1


def test_recent_trace_is_not_looked_up(session, context):
    lookups = []
    sink = old_sink(session, NOW, lookups=lookups)
    record_scenario(sink, context)
    sink.flush()
    assert lookups == []


def test_recent_trace_keeps_its_times(sink, session, context):
    conversation, *_ = record_scenario(sink, context)
    sink.flush()
    assert runs_by_id(session)[str(conversation.run_id)]["start_time"] == T0.isoformat()
