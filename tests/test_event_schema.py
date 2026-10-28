import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from agent_autonomy_lab.domain.events import (
    NO_GATE,
    SCHEMA_VERSION,
    EventType,
    ExperimentClass,
    GateDecision,
    GateEventClass,
    Outcome,
    TokenUsage,
    TrafficType,
    new_id,
)
from tests.conftest import T0, make_context, make_event

# Every field in the docs/ARCHITECTURE.md identifier table, plus the ticket's additions.
CONTRACT_FIELDS = {
    "event_id", "simulation_id", "thread_id", "scenario_id", "task_id", "turn_index",
    "run_id", "parent_run_id", "agent_version", "prompt_version", "model_id",
    "user_model_id", "tool_schema_version", "policy_version", "autonomy_level",
    "traffic_type", "workflow", "scenario_category", "tool_name", "argument_hash",
    "decision", "decision_reason", "outcome", "elapsed_ms", "cost",
    "error_class", "experiment_class", "fault_injected",
}  # fmt: skip


def test_schema_has_a_version_id():
    event = make_event(EventType.CONVERSATION)
    assert event.schema_version == SCHEMA_VERSION == "trace-event/1"
    assert event.to_dict()["schema_version"] == SCHEMA_VERSION


def test_record_covers_every_contract_field():
    record = make_event(EventType.CONVERSATION).to_dict()
    assert CONTRACT_FIELDS <= record.keys()
    # tokens are recorded as prompt and completion counts.
    assert {"prompt_tokens", "completion_tokens"} <= record.keys()


def test_record_is_json_serializable_with_utc_timestamp():
    event = make_event(
        EventType.MODEL_CALL,
        tokens=TokenUsage(prompt_tokens=1200, completion_tokens=80),
        cost=0.0012,
        elapsed_ms=850.0,
        inputs={"messages": [{"role": "user", "content": "Hi"}]},
    )
    record = json.loads(json.dumps(event.to_dict()))
    assert record["timestamp"] == "2026-10-27T06:25:09+00:00"
    assert record["prompt_tokens"] == 1200
    assert record["autonomy_level"] == "n/a"
    assert record["traffic_type"] == "synthetic"


def test_unsupported_schema_version_is_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        make_event(EventType.CONVERSATION, schema_version="trace-event/0")


def test_gate_event_classes_match_autonomy_md():
    assert [c.value for c in GateEventClass] == [
        "ATTEMPTED", "DENIED", "PENDING_APPROVAL", "APPROVED", "EXECUTED", "EXECUTION_FAILED",
    ]  # fmt: skip
    assert {d.value for d in GateDecision} == {"ALLOWED", "DENIED", "PENDING_APPROVAL"}


def test_experiment_class_and_traffic_type_values():
    assert {c.value for c in ExperimentClass} == {
        "official_upstream_baseline",
        "custom_lab_experiment",
    }
    assert TrafficType.SYNTHETIC == "synthetic"
    assert make_context().traffic_type is TrafficType.SYNTHETIC
    assert make_context().fault_injected is False


# autonomy_level


def test_no_gate_is_distinct_from_level_zero():
    no_gate = make_context(autonomy_level=NO_GATE)
    level_zero = make_context(autonomy_level=0)
    assert NO_GATE == "n/a"
    assert no_gate.autonomy_level != level_zero.autonomy_level
    assert no_gate != level_zero


@pytest.mark.parametrize("level", [0, 1, 2, 3, "n/a"])
def test_valid_autonomy_levels(level):
    assert make_context(autonomy_level=level).autonomy_level == level


@pytest.mark.parametrize("level", [-1, 4, "0", "L1", None, True, False, 1.0])
def test_invalid_autonomy_levels(level):
    with pytest.raises(ValueError, match="autonomy_level"):
        make_context(autonomy_level=level)


@pytest.mark.parametrize("name", ["simulation_id", "thread_id", "task_id"])
def test_context_requires_identifiers(name):
    with pytest.raises(ValueError, match=name):
        make_context(**{"thread_id": "thread-1", name: ""})


# timestamps


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="UTC"):
        make_event(EventType.CONVERSATION, timestamp=datetime(2026, 10, 27, 0, 25, 9))


def test_non_utc_timestamp_is_rejected():
    local = T0.astimezone(timezone(timedelta(hours=-6)))
    with pytest.raises(ValueError, match="UTC"):
        make_event(EventType.CONVERSATION, timestamp=local)


# span links


def test_conversation_is_a_root():
    with pytest.raises(ValueError, match="root"):
        make_event(EventType.CONVERSATION, parent_run_id=new_id())


@pytest.mark.parametrize("event_type", [EventType.MODEL_CALL, EventType.TOOL_CALL])
def test_child_spans_need_a_parent(event_type):
    with pytest.raises(ValueError, match="parent_run_id"):
        make_event(event_type, parent_run_id=None)


def test_span_cannot_be_its_own_parent():
    run_id = new_id()
    with pytest.raises(ValueError, match="cannot equal"):
        make_event(EventType.TOOL_CALL, run_id=run_id, parent_run_id=run_id)


# gate events


def test_gate_event_needs_a_class_and_tool():
    with pytest.raises(ValueError, match="gate_event"):
        make_event(EventType.GATE)
    with pytest.raises(ValueError, match="tool_name"):
        make_event(EventType.GATE, gate_event=GateEventClass.ATTEMPTED, tool_name=None)


def test_gate_event_has_no_parent_run():
    with pytest.raises(ValueError, match="no parent_run_id"):
        make_event(EventType.GATE, gate_event=GateEventClass.DENIED, parent_run_id=new_id())


def test_gate_fields_are_rejected_on_other_events():
    with pytest.raises(ValueError, match="gate events only"):
        make_event(EventType.TOOL_CALL, decision=GateDecision.DENIED)


def test_gate_decision_event():
    event = make_event(
        EventType.GATE,
        context=make_context(autonomy_level=2),
        tool_name="cancel_pending_order",
        gate_event=GateEventClass.DENIED,
        decision=GateDecision.DENIED,
        decision_reason="missing_customer_confirmation",
    )
    record = event.to_dict()
    assert record["gate_event"] == "DENIED"
    assert record["decision_reason"] == "missing_customer_confirmation"
    assert record["autonomy_level"] == 2


# tools, outcomes, and errors


def test_tool_call_needs_a_tool_name():
    with pytest.raises(ValueError, match="tool_name"):
        make_event(EventType.TOOL_CALL, tool_name=None)


def test_argument_hash_needs_a_tool_name():
    with pytest.raises(ValueError, match="argument_hash"):
        make_event(EventType.MODEL_CALL, argument_hash="sha256:00")


def test_tool_error_carries_class_and_message():
    event = make_event(
        EventType.TOOL_CALL,
        context=make_context(fault_injected=True),
        outcome=Outcome.ERROR,
        error_class="injected_fault",
        error_message="Error: tool temporarily unavailable",
    )
    record = event.to_dict()
    assert (record["outcome"], record["error_class"]) == ("error", "injected_fault")
    assert record["fault_injected"] is True


@pytest.mark.parametrize("outcome", [None, Outcome.SUCCESS, Outcome.REJECTED])
def test_error_fields_need_error_outcome(outcome):
    with pytest.raises(ValueError, match="outcome=error"):
        make_event(EventType.TOOL_CALL, outcome=outcome, error_class="tool_error")


def test_outcome_is_for_spans_only():
    with pytest.raises(ValueError, match="span events only"):
        make_event(EventType.GATE, gate_event=GateEventClass.EXECUTED, outcome=Outcome.SUCCESS)


@pytest.mark.parametrize("name", ["turn_index", "elapsed_ms", "cost"])
def test_negative_numbers_are_rejected(name):
    with pytest.raises(ValueError, match=name):
        make_event(EventType.MODEL_CALL, **{name: -1})


@pytest.mark.parametrize("name", ["elapsed_ms", "cost"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_metrics_are_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        make_event(EventType.MODEL_CALL, **{name: value})


def test_record_is_strict_json():
    event = make_event(EventType.MODEL_CALL, elapsed_ms=850.0, cost=0.0012)
    json.dumps(event.to_dict(), allow_nan=False)


def test_negative_tokens_are_rejected():
    with pytest.raises(ValueError, match="token"):
        TokenUsage(prompt_tokens=-1, completion_tokens=0)
    assert TokenUsage(prompt_tokens=10, completion_tokens=5).total_tokens == 15


def test_events_are_immutable():
    event = make_event(EventType.CONVERSATION)
    with pytest.raises(AttributeError):
        event.run_id = new_id()  # type: ignore[misc]
    assert "inputs" in {f.name for f in fields(event)}
