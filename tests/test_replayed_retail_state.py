"""The τ-bench replay adapter, against real retail tools and ``db.json``.

A run's record is produced by calling the tools on a separate live environment,
so the replay is compared with what a live environment really did.
"""

import hashlib
import json
import socket
from datetime import UTC, datetime
from typing import Any

import pytest
from tau2.data_model.message import ToolCall as Tau2ToolCall
from tau2.domains.retail.environment import get_environment
from tau2.domains.retail.utils import RETAIL_DB_PATH
from tau2.evaluator.evaluator_env import EnvironmentEvaluator
from tau2.registry import registry

from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.domain.retail_state import WriteOutcome
from agent_autonomy_lab.domain.simulations import (
    ExecutionStatus,
    Message,
    Role,
    SavedSimulation,
    ToolCall,
)
from agent_autonomy_lab.ports.retail_state import RetailStateSource

T0 = datetime(2026, 10, 31, tzinfo=UTC)
INITIAL_HASH = "b25c9cb211f5efcaee5dd646054a73c4a9f43f4f5acc32c10713cd9f9ac20e9c"
# Retail task 0's one write, as the agent made it in the Milestone 3 runs, and
# the live hash those runs recorded (reports/m3-traces/manifest.yaml).
TASK_0_ORDER = "#W2378156"
TASK_0_WRITE = {
    "order_id": TASK_0_ORDER,
    "item_ids": ["1151293680", "4983901480"],
    "new_item_ids": ["7706410293", "7747408585"],
    "payment_method_id": "credit_card_9513926",
}
TASK_0_LIVE_HASH = "a109be174767fde0ebb50fe3be37cdb2f1ce017fefd7bd29ea3e4d38bd3ac25a"
# A pending order paid by gift card, so cancelling it refunds the card at once.
GIFT_CARD_ORDER = "#W6779827"
GIFT_CARD_USER = "ethan_lopez_6291"
GIFT_CARD = "gift_card_7219486"
OTHER_PENDING_ORDER = "#W6426438"


def call_message(*calls: ToolCall) -> Message:
    return Message(role=Role.ASSISTANT, turn_index=0, timestamp=T0, tool_calls=calls)


def result_message(call_id: str, content: str | None, **fields: Any) -> Message:
    fields.setdefault("execution_status", ExecutionStatus.EXECUTED)
    return Message(
        role=Role.TOOL,
        turn_index=0,
        timestamp=T0,
        tool_call_id=call_id,
        content=content,
        **fields,
    )


def simulation_of(*messages: Message) -> SavedSimulation:
    return SavedSimulation("sim", "0", T0, 1.0, "user_stop", messages)


def live_record(*calls: tuple[str, dict]) -> tuple[SavedSimulation, str]:
    """Run the calls on a live environment; return the record and the live hash."""
    environment = get_environment()
    messages: list[Message] = []
    for index, (name, arguments) in enumerate(calls):
        call_id = f"call-{index}"
        response = environment.get_response(
            Tau2ToolCall(id=call_id, name=name, arguments=arguments)
        )
        messages.append(call_message(ToolCall(call_id, name, arguments)))
        messages.append(result_message(call_id, response.content, error=response.error))
    return simulation_of(*messages), environment.get_db_hash()


@pytest.fixture(scope="module")
def source() -> ReplayedRetailState:
    return ReplayedRetailState()


def test_the_adapter_satisfies_the_port(source):
    typed: RetailStateSource = source
    assert typed.timeline(simulation_of()).simulation_id == "sim"


def test_a_run_with_no_writes_ends_in_the_initial_state(source):
    timeline = source.timeline(simulation_of())
    assert timeline.writes == ()
    assert timeline.final.db_hash == timeline.replayed_hash == INITIAL_HASH


def test_task_0_write_reaches_the_live_hash_of_the_milestone_3_runs(source):
    simulation, live_hash = live_record(("exchange_delivered_order_items", TASK_0_WRITE))
    timeline = source.timeline(simulation)
    assert live_hash == TASK_0_LIVE_HASH
    assert timeline.replayed_hash == TASK_0_LIVE_HASH
    assert timeline.final.orders[TASK_0_ORDER]["status"] == "exchange requested"
    assert timeline.before("call-0").orders[TASK_0_ORDER]["status"] == "delivered"
    assert timeline.before("call-0").db_hash == INITIAL_HASH


def test_each_write_sees_the_balance_at_call_time(source):
    """The reason for the replay: the agent's reads do not hold a balance it never looked up."""
    cancel = {"order_id": GIFT_CARD_ORDER, "reason": "no longer needed"}
    other = {"order_id": OTHER_PENDING_ORDER, "reason": "ordered by mistake"}
    simulation, live_hash = live_record(
        ("cancel_pending_order", cancel), ("cancel_pending_order", other)
    )
    timeline = source.timeline(simulation)

    def balance(state):
        return state.users[GIFT_CARD_USER]["payment_methods"][GIFT_CARD]["balance"]

    assert balance(timeline.before("call-0")) == 49.0
    assert balance(timeline.before("call-1")) == 4128.45  # 49.00 + the 4,079.45 refund
    assert timeline.replayed_hash == live_hash
    assert not timeline.divergences


def test_a_failed_write_is_recorded_and_leaves_this_database_unchanged(source):
    arguments = {"order_id": TASK_0_ORDER, "reason": "no longer needed"}  # delivered, so refused
    simulation, live_hash = live_record(("cancel_pending_order", arguments))
    timeline = source.timeline(simulation)
    assert timeline.writes[0].outcome is WriteOutcome.FAILED
    assert timeline.replayed_hash == live_hash == INITIAL_HASH
    assert not timeline.divergences


def test_a_failed_write_that_changed_state_is_replayed_too(tmp_path, monkeypatch):
    """``modify_pending_order_payment`` raises after adding payments if the original method
    is gone. No order in ``db.json`` reaches that path, so this test removes the method."""
    data = json.loads(RETAIL_DB_PATH.read_text(encoding="utf-8"))
    order_id = next(
        key
        for key, order in data["orders"].items()
        if order["status"] == "pending"
        and len(data["users"][order["user_id"]]["payment_methods"]) > 1
    )
    order = data["orders"][order_id]
    methods = data["users"][order["user_id"]]["payment_methods"]
    original = order["payment_history"][0]["payment_method_id"]
    new_method = next(
        key
        for key, method in methods.items()
        if key != original and method["source"] != "gift_card"
    )
    del methods[original]
    db_path = tmp_path / "db.json"
    db_path.write_text(json.dumps(data), encoding="utf-8")

    source = ReplayedRetailState(db_path)
    call = ToolCall(
        "call-0",
        "modify_pending_order_payment",
        {"order_id": order_id, "payment_method_id": new_method},
    )
    recorded = result_message("call-0", "Error: Payment method not found", error=True)
    timeline = source.timeline(simulation_of(call_message(call), recorded))

    assert timeline.writes[0].outcome is WriteOutcome.FAILED
    assert not timeline.divergences
    assert len(timeline.final.orders[order_id]["payment_history"]) == 3
    assert timeline.replayed_hash != timeline.before("call-0").db_hash


def test_a_rejected_write_is_listed_but_not_run(source):
    call = ToolCall("call-0", "exchange_delivered_order_items", TASK_0_WRITE)
    rejected = result_message(
        "call-0", "denied", error=True, execution_status=ExecutionStatus.REJECTED
    )
    timeline = source.timeline(simulation_of(call_message(call), rejected))
    assert timeline.writes[0].outcome is WriteOutcome.REJECTED
    assert timeline.writes[0].state_before.db_hash == INITIAL_HASH
    assert timeline.replayed_hash == INITIAL_HASH
    assert not timeline.divergences


def test_a_write_with_no_result_withholds_later_state(source):
    unknown = ToolCall("call-0", "exchange_delivered_order_items", TASK_0_WRITE)
    later = ToolCall(
        "call-1",
        "cancel_pending_order",
        {"order_id": GIFT_CARD_ORDER, "reason": "no longer needed"},
    )
    timeline = source.timeline(
        simulation_of(call_message(unknown), call_message(later), result_message("call-1", "{}"))
    )
    assert timeline.writes[0].outcome is WriteOutcome.NO_RESULT
    assert timeline.before("call-0").db_hash == INITIAL_HASH
    assert timeline.before("call-1") is None
    assert timeline.final is None
    assert "no result" in timeline.divergences[0].reason


def test_a_result_that_differs_from_the_record_is_a_divergence(source):
    simulation, _ = live_record(("exchange_delivered_order_items", TASK_0_WRITE))
    call, _result = simulation.messages
    altered = result_message("call-0", json.dumps({"status": "something else"}))
    timeline = source.timeline(simulation_of(call, altered))
    assert timeline.before("call-0").db_hash == INITIAL_HASH  # before the divergence: still sound
    assert timeline.final is None
    assert timeline.replayed_hash == TASK_0_LIVE_HASH
    assert "differs" in timeline.divergences[0].reason


def test_an_error_flag_that_differs_from_the_record_is_a_divergence(source):
    """A write the record says failed but the replay ran, such as an injected fault."""
    call = ToolCall("call-0", "exchange_delivered_order_items", TASK_0_WRITE)
    failed = result_message("call-0", "Error: Service temporarily unavailable", error=True)
    timeline = source.timeline(simulation_of(call_message(call), failed))
    assert "error flag" in timeline.divergences[0].reason


def test_results_compare_as_data_not_text(source):
    simulation, _ = live_record(("exchange_delivered_order_items", TASK_0_WRITE))
    call, result = simulation.messages
    respaced = json.dumps(json.loads(result.content), sort_keys=True, indent=4)
    timeline = source.timeline(simulation_of(call, result_message("call-0", respaced)))
    assert not timeline.divergences


def test_status_reads_agree_with_the_replay_before_and_after_a_write(source):
    read = ("get_order_details", {"order_id": TASK_0_ORDER})
    write = ("exchange_delivered_order_items", TASK_0_WRITE)
    simulation, _ = live_record(read, write, read)
    timeline = source.timeline(simulation)
    assert timeline.status_reads_checked == 2
    assert not timeline.divergences


def test_a_status_read_the_replay_disagrees_with_is_a_divergence(source):
    call = ToolCall("call-0", "get_order_details", {"order_id": TASK_0_ORDER})
    read = result_message("call-0", json.dumps({"status": "pending"}))
    write = ToolCall("call-1", "exchange_delivered_order_items", TASK_0_WRITE)
    timeline = source.timeline(simulation_of(call_message(call), read, call_message(write)))
    assert "'pending'" in timeline.divergences[0].reason
    assert timeline.before("call-1") is None
    assert timeline.final is None


def test_a_status_read_of_an_order_the_replay_lacks_is_a_divergence(source):
    call = ToolCall("call-0", "get_order_details", {"order_id": "#W0"})
    read = result_message("call-0", json.dumps({"status": "pending"}))
    timeline = source.timeline(simulation_of(call_message(call), read))
    assert "does not have" in timeline.divergences[0].reason


def test_a_failed_status_read_has_no_status_to_check(source):
    """The injected fault of Milestone 3: the agent saw an error, so it read nothing."""
    call = ToolCall("call-0", "get_order_details", {"order_id": TASK_0_ORDER})
    failed = result_message("call-0", "Error: Service temporarily unavailable", error=True)
    timeline = source.timeline(simulation_of(call_message(call), failed))
    assert timeline.status_reads_checked == 0
    assert not timeline.divergences


def test_reads_and_unknown_tools_are_not_writes(source):
    calls = (
        ToolCall("call-0", "find_user_id_by_email", {"email": "nobody@example.com"}),
        ToolCall("call-1", "made_up_tool", {}),
        ToolCall("call-2", "transfer_to_human_agents", {"summary": "help"}),
    )
    results = [result_message(c.call_id, "Error", error=True) for c in calls]
    timeline = source.timeline(simulation_of(call_message(*calls), *results))
    assert timeline.writes == ()
    assert timeline.replayed_hash == INITIAL_HASH


def test_several_calls_in_one_message_pair_with_their_own_results(source):
    """Policy line 20 forbids this, but a record can hold it, with results in any order."""
    environment = get_environment()
    write = ToolCall("call-w", "exchange_delivered_order_items", TASK_0_WRITE)
    read = ToolCall("call-r", "get_order_details", {"order_id": TASK_0_ORDER})
    write_result = environment.get_response(
        Tau2ToolCall(id="call-w", name=write.name, arguments=dict(write.arguments))
    )
    read_result = environment.get_response(
        Tau2ToolCall(id="call-r", name=read.name, arguments=dict(read.arguments))
    )
    timeline = source.timeline(
        simulation_of(
            call_message(write, read),
            result_message("call-r", read_result.content),
            result_message("call-w", write_result.content),
        )
    )
    assert timeline.writes[0].outcome is WriteOutcome.EXECUTED
    assert timeline.status_reads_checked == 1
    assert not timeline.divergences


def test_the_mutating_tools_are_the_seven_write_tools():
    """If τ-bench's classification moves, the 'write' the contract names would too."""
    tools = get_environment().tools
    assert {name for name in tools.tools if tools.tool_mutates_state(name)} == {
        "cancel_pending_order",
        "exchange_delivered_order_items",
        "modify_pending_order_address",
        "modify_pending_order_items",
        "modify_pending_order_payment",
        "modify_user_address",
        "return_delivered_order_items",
    }


def test_replays_are_independent_and_snapshots_are_detached(source):
    simulation, _ = live_record(("exchange_delivered_order_items", TASK_0_WRITE))
    first = source.timeline(simulation)
    first.final.orders[TASK_0_ORDER]["status"] = "scribbled on"
    second = source.timeline(simulation)
    assert second.final.orders[TASK_0_ORDER]["status"] == "exchange requested"
    assert second.before("call-0").db_hash == INITIAL_HASH


def test_replay_calls_no_model_and_leaves_registry_and_evaluation_alone(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("replay must stay offline and out of evaluation")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("litellm.completion", forbidden)
    monkeypatch.setattr("tau2.utils.llm_utils.completion", forbidden)
    monkeypatch.setattr(EnvironmentEvaluator, "calculate_reward", forbidden)

    file_hash = hashlib.sha256(RETAIL_DB_PATH.read_bytes()).hexdigest()
    constructor = registry.get_env_constructor("retail")
    names = registry.get_info().domains
    simulation, _ = live_record(("exchange_delivered_order_items", TASK_0_WRITE))

    timeline = ReplayedRetailState().timeline(simulation)

    assert timeline.replayed_hash == TASK_0_LIVE_HASH
    assert registry.get_env_constructor("retail") is constructor
    assert registry.get_info().domains == names
    assert hashlib.sha256(RETAIL_DB_PATH.read_bytes()).hexdigest() == file_hash
    assert constructor().get_db_hash() == INITIAL_HASH
