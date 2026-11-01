"""The write-eligibility checks, on fixtures.

Three kinds of record: small synthetic ones with a canned state, for the shapes no
recorded run has; records played through the real retail tools, to show what the
tools accept and the checks catch; and the Milestone 2 transcripts of tasks ``0``
and ``2``, the source of the prototype's mutations (docs/BASELINE.md, "What the
baseline run showed").
"""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from tau2.data_model.message import ToolCall as Tau2ToolCall
from tau2.domains.retail.environment import get_environment

from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import USER_ACCESS_KEY, UserAccessEvaluator
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.export_simulation import evidence_trace
from agent_autonomy_lab.application.write_eligibility import (
    ALLOWED_STATUS,
    GIFT_CARD_BALANCE_KEY,
    ITEM_CHANGE_ONCE_KEY,
    ITEM_VARIANT_KEY,
    ORDER_STATUS_KEY,
    PAYMENT_CHANGE_KEY,
    REFUND_METHOD_KEY,
    STATUS_READ_KEY,
    GiftCardBalanceEvaluator,
    ItemChangeOnceEvaluator,
    ItemVariantEvaluator,
    OrderStatusEvaluator,
    PaymentChangeEvaluator,
    RefundMethodEvaluator,
    StatusReadEvaluator,
    write_eligibility_evaluators,
)
from agent_autonomy_lab.domain.evaluation import EvaluationSubject, FailureKind, ResultStatus
from agent_autonomy_lab.domain.events import EventType, ExperimentClass
from agent_autonomy_lab.domain.retail_state import (
    ReplayDivergence,
    RetailState,
    RetailStateTimeline,
    WriteAttempt,
    WriteOutcome,
)
from agent_autonomy_lab.domain.simulations import SavedRun, ToolCall
from agent_autonomy_lab.domain.tool_timeline import WRITE_TOOLS, tool_timeline
from agent_autonomy_lab.ports.retail_state import CannedRetailState
from tests.test_access_checks import (
    ME,
    OTHER,
    SIM,
    TASK_0,
    UTC_ZONE,
    _agent,
    _events,
    _failed,
    _result,
    _simulation,
    _statuses,
    _steps,
    _subject,
    _task_0_events,
    _user,
    _write_call,
)

PASS, FAIL = ResultStatus.PASS, ResultStatus.FAIL
UNKNOWN, NOT_APPLICABLE = ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.NOT_APPLICABLE

TASK_2 = json.loads(
    (Path(__file__).parent / "fixtures" / "m2-task-2" / "simulation.json").read_text("utf-8")
)


def _order(status: str, **fields) -> dict:
    return {
        "user_id": ME,
        "status": status,
        "items": [{"item_id": "A", "product_id": "P", "price": 10.0}],
        "payment_history": [
            {"transaction_type": "payment", "amount": 10.0, "payment_method_id": "card"}
        ],
        **fields,
    }


STATE = RetailState(
    db_hash="h",
    orders={
        "#PENDING": _order("pending"),
        "#DELIVERED": _order("delivered"),
        "#MODIFIED": _order("pending (item modified)"),
        "#REQUESTED": _order("exchange requested"),
        "#PROCESSED": _order("processed"),
        "#NO_STATUS": {"user_id": ME},
        "#NO_OWNER": _order("pending", user_id="ghost"),
        "#NO_PAYMENT": _order("pending", payment_history=[]),
        "#NO_PRODUCT": _order("delivered", items=[{"item_id": "A", "product_id": "ghost"}]),
        "#TWO_PAYMENTS": _order(
            "pending",
            payment_history=[
                {"transaction_type": "payment", "amount": 10.0, "payment_method_id": "card"},
                {"transaction_type": "refund", "amount": 2.0, "payment_method_id": "card"},
            ],
        ),
    },
    users={
        ME: {
            "payment_methods": {
                "card": {"source": "credit_card"},
                "paypal": {"source": "paypal"},
                "rich": {"source": "gift_card", "balance": 100.0},
                "exact": {"source": "gift_card", "balance": 20.0},
                "poor": {"source": "gift_card", "balance": 1.0},
                "broken": {"source": "gift_card"},
            }
        },
        OTHER: {"payment_methods": {"theirs": {"source": "gift_card", "balance": 100.0}}},
    },
    products={
        "P": {
            "variants": {
                "A": {"price": 10.0, "available": True},
                "B": {"price": 30.0, "available": True},
                "C": {"price": 5.0, "available": True},
                "GONE": {"price": 10.0, "available": False},
            }
        },
        "Q": {"variants": {"X": {"price": 10.0, "available": True}}},
    },
)


def _read(order_id: str = "#PENDING", content: str = "{}", *error) -> tuple:
    return ("get_order_details", {"order_id": order_id}, content, *error)


def _cancel(order_id: str = "#PENDING", *error) -> tuple:
    arguments = {"order_id": order_id, "reason": "ordered by mistake"}
    return ("cancel_pending_order", arguments, "{}", *error)


def _address(order_id: str = "#PENDING") -> tuple:
    return ("modify_pending_order_address", {"order_id": order_id, "address1": "x"}, "{}")


def _item_change(tool: str, order_id: str, old, new, method: str = "card", *error) -> tuple:
    arguments = {
        "order_id": order_id,
        "item_ids": old,
        "new_item_ids": new,
        "payment_method_id": method,
    }
    return (tool, arguments, "{}", *error)


def _exchange(order_id="#DELIVERED", old=("A",), new=("B",), method="card", *error) -> tuple:
    tool = "exchange_delivered_order_items"
    return _item_change(tool, order_id, list(old), list(new), method, *error)


def _modify(order_id="#PENDING", old=("A",), new=("B",), method="card", *error) -> tuple:
    tool = "modify_pending_order_items"
    return _item_change(tool, order_id, list(old), list(new), method, *error)


def _payment(method: str = "paypal", order_id: str = "#PENDING") -> tuple:
    arguments = {"order_id": order_id, "payment_method_id": method}
    return ("modify_pending_order_payment", arguments, "{}")


def _return(method: str = "card", order_id: str = "#DELIVERED") -> tuple:
    arguments = {"order_id": order_id, "item_ids": ["A"], "payment_method_id": method}
    return ("return_delivered_order_items", arguments, "{}")


def _timeline(messages, state: RetailState | None = STATE) -> RetailStateTimeline:
    """The same canned state before every write, or none the replay vouches for."""
    writes = tuple(
        WriteAttempt(r.call.call_id, r.call.name, WriteOutcome.EXECUTED, state)
        for r in tool_timeline(messages)
        if r.call.name in WRITE_TOOLS
    )
    if state is None:
        divergence = ReplayDivergence("c", "unknown", "lost")
        return RetailStateTimeline(SIM, writes, None, "h", divergences=(divergence,))
    return RetailStateTimeline(SIM, writes, state, state.db_hash)


def _state_check(evaluator_class, *steps, state=STATE, events=None):
    messages = _steps(*steps)
    source = CannedRetailState({SIM: _timeline(messages, state)})
    return evaluator_class(source).evaluate(_subject(messages, events=events)).results


def _timeline_check(evaluator_class, messages, events=None):
    return evaluator_class().evaluate(_subject(messages, events=events)).results


def _with_lost_call(evaluator_class, messages):
    """The check on a record with a result that has no call, keeping the rest of its trace."""
    simulation = _simulation(messages)
    run = SavedRun("run", None, None, None, None, (simulation,))
    events = evidence_trace(run, simulation, ExperimentClass.CUSTOM_LAB_EXPERIMENT)
    return evaluator_class().evaluate(EvaluationSubject(SIM, simulation, events)).results


def _status_read(*steps):
    return _timeline_check(StatusReadEvaluator, _steps(*steps))


def _once(*steps):
    return _timeline_check(ItemChangeOnceEvaluator, _steps(*steps))


def _lost_call(call: ToolCall, messages) -> tuple:
    """``messages`` with a call added that has no result, in its own turn."""
    return (*messages, _agent(len(messages), call))


def _continue(messages, *steps) -> tuple:
    """``messages``, then ``steps`` with call IDs and turns that follow on from them."""
    extra = []
    for number, step in enumerate(steps):
        name, arguments, content, *rest = step
        call_id = f"later-{number}"
        turn = len(messages) + len(extra)
        extra.append(_agent(turn, ToolCall(call_id, name, arguments)))
        extra.append(_result(turn + 1, call_id, content, error=bool(rest)))
    return (*messages, *extra)


# Status read (PA-11)


def test_a_read_before_the_write_passes_and_cites_both_spans():
    messages = _steps(_read(), _cancel())
    subject = _subject(messages)
    result = StatusReadEvaluator().evaluate(subject).results[0]
    assert result.status is PASS
    by_id = {event.event_id: event for event in subject.events}
    assert [by_id[i].tool_name for i in result.evidence_ids] == [
        "get_order_details",
        "cancel_pending_order",
    ]


def test_no_read_fails_as_a_violation():
    result = _status_read(_cancel())[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_a_read_of_another_order_does_not_count():
    assert _statuses(_status_read(_read("#DELIVERED"), _cancel())) == [FAIL]


def test_a_failed_read_does_not_count():
    assert _statuses(_status_read(_read("#PENDING", "Error: x", True), _cancel())) == [FAIL]


def test_a_read_from_before_an_earlier_write_is_stale():
    results = _status_read(_read(), _address(), _cancel())
    assert _statuses(results) == [PASS, FAIL]


def test_a_new_read_after_the_write_is_current_again():
    results = _status_read(_read(), _address(), _read(), _cancel())
    assert _statuses(results) == [PASS, PASS]


def test_a_write_to_another_order_does_not_make_a_read_stale():
    results = _status_read(_read(), _read("#DELIVERED"), _exchange(), _cancel())
    assert _statuses(results) == [PASS, PASS]


def test_a_failed_write_does_not_make_a_read_stale():
    results = _status_read(_read(), _cancel("#PENDING", True), _cancel())
    assert _statuses(results) == [PASS, PASS]


def test_a_user_address_change_is_not_a_target():
    change = ("modify_user_address", {"user_id": ME, "address1": "x"}, "{}")
    assert _statuses(_status_read(change)) == [NOT_APPLICABLE]


def test_a_read_in_the_writes_own_message_had_not_returned():
    read = ToolCall("r", "get_order_details", {"order_id": "#PENDING"})
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#PENDING"})
    messages = (_user(0), _agent(1, read, write), _result(2, "r", "{}"), _result(3, "w", "{}"))
    assert _statuses(_timeline_check(StatusReadEvaluator, messages)) == [FAIL]


def test_an_earlier_read_still_counts_beside_a_second_read_in_the_writes_message():
    read = ToolCall("r", "get_order_details", {"order_id": "#PENDING"})
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#PENDING"})
    first = _steps(_read())
    turn = len(first)
    messages = (
        *first,
        _agent(turn, read, write),
        _result(turn + 1, "r", "{}"),
        _result(turn + 2, "w", "{}"),
    )
    assert _statuses(_timeline_check(StatusReadEvaluator, messages)) == [PASS]


def test_a_read_with_no_result_may_be_the_missing_one():
    lost = ToolCall("lost", "get_order_details", {"order_id": "#PENDING"})
    messages = _continue(_lost_call(lost, _steps()), _cancel())
    assert _statuses(_timeline_check(StatusReadEvaluator, messages)) == [UNKNOWN]


def test_a_write_with_no_result_may_have_made_the_read_stale_until_a_new_read():
    lost = ToolCall("lost", "modify_pending_order_address", {"order_id": "#PENDING"})
    messages = _lost_call(lost, _steps(_read()))
    undecided = _continue(messages, _cancel())
    results = _timeline_check(StatusReadEvaluator, undecided)
    assert _statuses(results) == [UNKNOWN, UNKNOWN]
    settled = _continue(messages, _read(), _cancel())
    assert _statuses(_timeline_check(StatusReadEvaluator, settled)) == [UNKNOWN, PASS]


def test_a_write_with_no_read_and_no_result_is_still_a_violation():
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#PENDING"})
    assert _statuses(_timeline_check(StatusReadEvaluator, (_user(0), _agent(1, write)))) == [FAIL]


def test_a_write_with_a_read_but_no_result_is_insufficient():
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#PENDING"})
    messages = _lost_call(write, _steps(_read()))
    assert _statuses(_timeline_check(StatusReadEvaluator, messages)) == [UNKNOWN]


def test_a_lost_call_before_the_write_leaves_a_missing_read_undecided():
    messages = (_user(0), _result(1, "lost", "{}"), *_steps(_cancel())[1:])
    subject = EvaluationSubject(SIM, _simulation(messages), ())
    assert _statuses(StatusReadEvaluator().evaluate(subject).results) == [UNKNOWN, UNKNOWN]


def test_a_lost_call_between_the_read_and_the_write_stops_a_pass():
    first = _steps(_read())
    messages = _continue((*first, _result(len(first), "lost", "{}")), _cancel())
    assert _statuses(_with_lost_call(StatusReadEvaluator, messages)) == [UNKNOWN, UNKNOWN]


def test_a_new_read_after_a_lost_call_settles_it():
    first = _steps(_read())
    messages = _continue((*first, _result(len(first), "lost", "{}")), _read(), _cancel())
    assert _statuses(_with_lost_call(StatusReadEvaluator, messages)) == [UNKNOWN, PASS]


def test_a_lost_call_before_the_last_write_cannot_supply_the_missing_read():
    messages = _continue((_user(0), _result(1, "lost", "{}")), _read(), _address(), _cancel())
    assert _statuses(_with_lost_call(StatusReadEvaluator, messages)) == [UNKNOWN, PASS, FAIL]


def test_a_lost_call_after_the_last_write_could_be_the_missing_read():
    first = _steps(_read(), _address())
    messages = _continue((*first, _result(len(first), "lost", "{}")), _cancel())
    assert _statuses(_with_lost_call(StatusReadEvaluator, messages)) == [UNKNOWN, PASS, UNKNOWN]


def test_a_lost_call_before_the_read_does_not_stop_a_pass():
    messages = _continue((_user(0), _result(1, "lost", "{}")), _read(), _cancel())
    assert _statuses(_with_lost_call(StatusReadEvaluator, messages)) == [UNKNOWN, PASS]


@pytest.mark.parametrize("arguments", [{}, {"order_id": 7}, {"order_id": ["#PENDING"]}])
def test_a_write_naming_no_order_is_insufficient(arguments):
    step = ("cancel_pending_order", arguments, "Error", True)
    assert _statuses(_status_read(step)) == [UNKNOWN]


def test_a_read_naming_no_usable_order_counts_for_nothing():
    unusable = ("get_order_details", {"order_id": 7}, "{}")
    assert _statuses(_status_read(unusable, _cancel())) == [FAIL]


def test_no_spans_cannot_support_a_status_read_result():
    messages = _steps(_read(), _cancel())
    assert _statuses(_timeline_check(StatusReadEvaluator, messages, events=())) == [UNKNOWN]
    unread = _steps(_cancel())
    assert _statuses(_timeline_check(StatusReadEvaluator, unread, events=())) == [UNKNOWN]


# Order status (PA-4)


def _status(*steps, **kwargs):
    return _state_check(OrderStatusEvaluator, *steps, **kwargs)


@pytest.mark.parametrize(
    "write",
    [_cancel(), _address(), _modify(), _payment(), _return(), _exchange()],
    ids=lambda write: write[0],
)
def test_the_exact_status_passes_for_every_order_write(write):
    assert _statuses(_status(write)) == [PASS]


@pytest.mark.parametrize(
    "write",
    [
        _cancel("#DELIVERED"),
        _cancel("#MODIFIED"),
        _address("#MODIFIED"),
        _payment("paypal", "#MODIFIED"),
        _modify("#MODIFIED"),
        _exchange("#PENDING"),
        _return("card", "#REQUESTED"),
        _cancel("#PROCESSED"),
    ],
    ids=lambda write: f"{write[0]}-{write[1]['order_id']}",
)
def test_any_other_status_fails_as_a_violation(write):
    result = _status(write)[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_a_forbidden_status_counts_even_when_the_tool_rejected_it():
    assert _statuses(_status(_cancel("#DELIVERED", True))) == [FAIL]


def test_every_write_that_targets_an_order_has_an_allowed_status():
    assert set(ALLOWED_STATUS) == WRITE_TOOLS - {"modify_user_address"}


def test_a_user_address_change_has_no_order_status():
    change = ("modify_user_address", {"user_id": ME, "address1": "x"}, "{}")
    assert _statuses(_status(change)) == [NOT_APPLICABLE]


@pytest.mark.parametrize(
    "write",
    [_cancel("#NOPE"), _cancel("#NO_STATUS"), ("cancel_pending_order", {"order_id": 7}, "{}")],
    ids=["unknown-order", "no-status", "no-order-id"],
)
def test_an_order_with_no_known_status_is_insufficient(write):
    assert _statuses(_status(write)) == [UNKNOWN]


def test_no_vouched_state_is_insufficient_not_a_failure():
    assert _statuses(_status(_cancel("#DELIVERED"), state=None)) == [UNKNOWN]


def test_a_forbidden_status_with_no_result_is_still_a_violation():
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#DELIVERED"})
    messages = (_user(0), _agent(1, write))
    source = CannedRetailState({SIM: _timeline(messages)})
    assert _statuses(OrderStatusEvaluator(source).evaluate(_subject(messages)).results) == [FAIL]


def test_an_allowed_status_with_no_result_is_insufficient():
    write = ToolCall("w", "cancel_pending_order", {"order_id": "#PENDING"})
    messages = (_user(0), _agent(1, write))
    source = CannedRetailState({SIM: _timeline(messages)})
    assert _statuses(OrderStatusEvaluator(source).evaluate(_subject(messages)).results) == [UNKNOWN]


def test_a_repeated_call_id_has_no_state_to_read():
    first = ToolCall("same", "cancel_pending_order", {"order_id": "#PENDING"})
    second = ToolCall("same", "cancel_pending_order", {"order_id": "#DELIVERED"})
    messages = (_user(0), _agent(1, first), _result(2, "same", "{}"), _agent(3, second))
    source = CannedRetailState({SIM: _timeline(messages)})
    results = OrderStatusEvaluator(source).evaluate(_subject(messages)).results
    assert _statuses(results) == [UNKNOWN, UNKNOWN]


def test_no_span_to_cite_is_insufficient():
    assert _statuses(_status(_cancel(), events=())) == [UNKNOWN]
    assert _statuses(_status(_cancel("#DELIVERED"), events=())) == [UNKNOWN]


# Once per order (PA-5, line 84)


def test_the_first_item_change_passes():
    assert _statuses(_once(_exchange())) == [PASS]
    assert _statuses(_once(_modify())) == [PASS]


@pytest.mark.parametrize("second", [_exchange(), _exchange("#DELIVERED", "A", "C", "card", True)])
def test_a_second_item_change_fails_and_cites_the_first(second):
    messages = _steps(_exchange(), second)
    subject = _subject(messages)
    results = ItemChangeOnceEvaluator().evaluate(subject).results
    assert _statuses(results) == [PASS, FAIL]
    assert results[1].failure_kind is FailureKind.VIOLATION
    by_id = {event.event_id: event for event in subject.events}
    assert results[0].evidence_ids[0] in results[1].evidence_ids
    assert {by_id[i].event_type for i in results[1].evidence_ids} == {
        EventType.MODEL_CALL,
        EventType.TOOL_CALL,
    }


def test_a_modification_then_an_exchange_on_one_order_is_a_second_change():
    second = _exchange("#PENDING")
    assert _statuses(_once(_modify(), second)) == [PASS, FAIL]


def test_a_retry_after_a_failed_attempt_is_not_a_second_change():
    failed = _exchange("#DELIVERED", "A", "GONE", "card", True)
    assert _statuses(_once(failed, _exchange())) == [PASS, PASS]


def test_item_changes_on_two_orders_are_each_a_first():
    assert _statuses(_once(_modify(), _exchange())) == [PASS, PASS]


def test_other_writes_do_not_use_up_the_change():
    assert _statuses(_once(_address(), _modify())) == [PASS]


def test_no_item_change_is_not_applicable():
    assert _statuses(_once(_cancel())) == [NOT_APPLICABLE]


def test_an_earlier_change_with_no_result_leaves_the_next_undecided():
    lost = ToolCall(
        "lost", "exchange_delivered_order_items", {"order_id": "#DELIVERED", "item_ids": ["A"]}
    )
    messages = _continue(_lost_call(lost, _steps()), _exchange())
    assert _statuses(_timeline_check(ItemChangeOnceEvaluator, messages)) == [UNKNOWN, UNKNOWN]


def test_a_lost_call_stops_a_pass_but_not_a_proved_second_change():
    messages = _continue((_user(0), _result(1, "lost", "{}")), _exchange(), _exchange())
    assert _statuses(_with_lost_call(ItemChangeOnceEvaluator, messages)) == [UNKNOWN, UNKNOWN, FAIL]


def test_an_item_change_naming_no_order_is_insufficient():
    step = ("exchange_delivered_order_items", {"item_ids": ["A"]}, "Error", True)
    assert _statuses(_once(step)) == [UNKNOWN]


def test_a_second_change_with_no_span_for_the_first_cannot_be_cited():
    messages = _steps(_exchange(), _exchange())
    events = tuple(
        e
        for e in _events(_simulation(messages))
        if not (e.event_type is EventType.TOOL_CALL and e.turn_index == 2)
    )
    results = _timeline_check(ItemChangeOnceEvaluator, messages, events=events)
    assert _statuses(results) == [UNKNOWN, UNKNOWN]


# Item variants (PA-5, lines 112 and 132)


def _variant(*steps, **kwargs):
    return _state_check(ItemVariantEvaluator, *steps, **kwargs)


def test_a_different_available_variant_passes():
    assert _statuses(_variant(_exchange(), _modify())) == [PASS, PASS]


@pytest.mark.parametrize(
    ("new", "phrase"),
    [
        ("A", "identical"),
        ("X", "not a variant"),
        ("NOWHERE", "not a variant"),
        ("GONE", "not available"),
    ],
)
@pytest.mark.parametrize("write", [_exchange, _modify])
def test_a_bad_new_item_fails_as_a_violation(write, new, phrase):
    result = _variant(write(new=(new,)))[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION
    assert phrase in result.reason


def test_one_write_has_one_result_that_names_every_bad_pair():
    order = {
        **STATE.orders,
        "#TWO": _order(
            "delivered",
            items=[
                {"item_id": "A", "product_id": "P", "price": 10.0},
                {"item_id": "C", "product_id": "P", "price": 5.0},
            ],
        ),
    }
    state = replace(STATE, orders=order)
    results = _variant(_exchange("#TWO", ("A", "C"), ("A", "C")), state=state)
    assert _statuses(results) == [FAIL]
    assert "item A" in results[0].reason
    assert "item C" in results[0].reason


@pytest.mark.parametrize(
    "write",
    [
        _exchange("#DELIVERED", ("A", "A"), ("B",)),
        _exchange("#DELIVERED", (), ()),
        ("exchange_delivered_order_items", {"order_id": "#DELIVERED"}, "{}"),
        _item_change("exchange_delivered_order_items", "#DELIVERED", "A", "B"),
        _exchange("#DELIVERED", ("MISSING",), ("B",)),
        _exchange("#NO_PRODUCT"),
        _exchange("#NOPE"),
    ],
    ids=["unequal", "empty", "absent", "not-lists", "not-in-order", "no-product", "no-order"],
)
def test_items_that_cannot_be_compared_are_insufficient(write):
    assert _statuses(_variant(write)) == [UNKNOWN]


def test_a_proved_bad_item_beats_an_unknown_one():
    results = _variant(_exchange("#DELIVERED", ("A", "MISSING"), ("A", "B")))
    assert _statuses(results) == [FAIL]


def test_no_vouched_state_gives_no_verdict_on_an_item():
    assert _statuses(_variant(_exchange(new=("A",)), state=None)) == [UNKNOWN]


def test_no_item_change_has_no_variant_to_check():
    assert _statuses(_variant(_cancel())) == [NOT_APPLICABLE]


# Payment change (line 102)


def _change(*steps, **kwargs):
    return _state_check(PaymentChangeEvaluator, *steps, **kwargs)


def test_a_different_method_of_the_owner_passes():
    assert _statuses(_change(_payment("paypal"))) == [PASS]


@pytest.mark.parametrize(
    ("write", "phrase"),
    [
        (_payment("card"), "original"),
        (_payment("theirs"), "not a method"),
        (_payment("nothing"), "not a method"),
        (_payment("paypal", "#TWO_PAYMENTS"), "one prior payment"),
    ],
)
def test_a_payment_change_breaking_line_102_fails(write, phrase):
    result = _change(write)[0]
    assert result.status is FAIL
    assert phrase in result.reason


@pytest.mark.parametrize(
    "write",
    [
        _payment("paypal", "#NO_OWNER"),
        _payment("paypal", "#NO_PAYMENT"),
        _payment("paypal", "#NOPE"),
        ("modify_pending_order_payment", {"order_id": "#PENDING"}, "{}"),
    ],
    ids=["no-owner", "no-payment", "no-order", "no-method"],
)
def test_a_payment_change_that_cannot_be_compared_is_insufficient(write):
    assert _statuses(_change(write)) == [UNKNOWN]


def test_no_payment_change_is_not_applicable():
    assert _statuses(_change(_cancel())) == [NOT_APPLICABLE]


# Gift-card balance (lines 104, 114, 134)


def _balance(*steps, **kwargs):
    return _state_check(GiftCardBalanceEvaluator, *steps, **kwargs)


@pytest.mark.parametrize(
    "write",
    [
        _payment("rich"),
        _exchange(method="rich"),
        _modify(method="rich"),
        _exchange(method="exact"),  # exactly the difference of 20
        _exchange(new=("C",), method="poor"),  # a refund of 5 needs no balance
    ],
)
def test_a_gift_card_that_covers_the_amount_passes(write):
    assert _statuses(_balance(write)) == [PASS]


@pytest.mark.parametrize(
    "write",
    [_payment("poor"), _exchange(method="poor"), _modify(method="poor")],
    ids=lambda write: write[0],
)
def test_a_gift_card_that_does_not_cover_it_fails(write):
    result = _balance(write)[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION
    assert "1.00" in result.reason


@pytest.mark.parametrize(
    "write", [_payment("paypal"), _exchange(), _modify(), _cancel(), _return("rich")]
)
def test_a_write_that_pays_with_no_gift_card_has_no_target(write):
    assert _statuses(_balance(write)) == [NOT_APPLICABLE]


@pytest.mark.parametrize(
    "write",
    [
        _payment("broken"),
        _payment("rich", "#NO_PAYMENT"),
        _payment("theirs"),
        _payment("rich", "#NO_OWNER"),
        _exchange(method="rich", old=("MISSING",)),
        _exchange(method="rich", new=("NOWHERE",)),
        _exchange("#DELIVERED", ("A", "A"), ("B",), "rich"),
    ],
    ids=["balance", "total", "not-owners", "owner", "old-item", "new-item", "unpaired"],
)
def test_an_amount_or_method_that_is_unknown_is_insufficient(write):
    assert _statuses(_balance(write)) == [UNKNOWN]


def test_no_vouched_state_gives_no_verdict_on_a_balance():
    assert _statuses(_balance(_payment("poor"), state=None)) == [UNKNOWN]


# Refund method (line 124)


def _refund(*steps, **kwargs):
    return _state_check(RefundMethodEvaluator, *steps, **kwargs)


@pytest.mark.parametrize("method", ["card", "rich", "poor"])
def test_the_original_method_or_a_gift_card_of_the_owner_passes(method):
    assert _statuses(_refund(_return(method))) == [PASS]


@pytest.mark.parametrize("method", ["paypal", "theirs", "nothing"])
def test_any_other_refund_method_fails(method):
    result = _refund(_return(method))[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


@pytest.mark.parametrize(
    "write",
    [
        _return("card", "#NO_OWNER"),
        _return("card", "#NO_PAYMENT"),
        _return("card", "#NOPE"),
        ("return_delivered_order_items", {"order_id": "#DELIVERED"}, "{}"),
    ],
    ids=["no-owner", "no-payment", "no-order", "no-method"],
)
def test_a_refund_that_cannot_be_compared_is_insufficient(write):
    assert _statuses(_refund(write)) == [UNKNOWN]


def test_no_return_is_not_applicable():
    assert _statuses(_refund(_cancel())) == [NOT_APPLICABLE]


# Every check


def _all_evaluators():
    source = CannedRetailState({SIM: _timeline(_steps())})
    return write_eligibility_evaluators(source)


@pytest.mark.parametrize("evaluator", _all_evaluators(), ids=lambda e: e.evaluation_key)
@pytest.mark.parametrize(
    "subject",
    [EvaluationSubject(SIM), EvaluationSubject(SIM, _simulation(()))],
    ids=["no-record", "no-messages"],
)
def test_a_run_with_no_messages_gives_insufficient_evidence(evaluator, subject):
    assert _statuses(evaluator.evaluate(subject).results) == [UNKNOWN]


def test_the_checks_of_a_simulation_share_one_replay():
    messages = _steps(_cancel())
    source = CannedRetailState({SIM: _timeline(messages)})
    subject = _subject(messages)
    evaluate_simulation(subject, write_eligibility_evaluators(source))
    assert len(source.simulations) == 1


def test_each_simulation_gets_its_own_replay():
    first, second = _steps(_cancel()), _steps(_cancel("#DELIVERED"))
    source = CannedRetailState({SIM: _timeline(first)})
    evaluators = write_eligibility_evaluators(source)
    for messages in (first, second):
        evaluate_simulation(_subject(messages), evaluators)
    assert len(source.simulations) == 2


def test_every_decided_result_cites_events_in_the_trace():
    steps = (
        _read(),
        _cancel(),
        _exchange(new=("A",)),
        _exchange(method="poor"),
        _payment("rich"),
        _return("paypal"),
    )
    messages = _steps(*steps)
    subject = _subject(messages)
    source = CannedRetailState({SIM: _timeline(messages)})
    results = evaluate_simulation(subject, write_eligibility_evaluators(source)).results
    known = {event.event_id for event in subject.events}
    decided = [r for r in results if r.status in (PASS, FAIL)]
    assert {r.evaluation_key for r in decided} == {
        STATUS_READ_KEY,
        ORDER_STATUS_KEY,
        ITEM_CHANGE_ONCE_KEY,
        ITEM_VARIANT_KEY,
        PAYMENT_CHANGE_KEY,
        GIFT_CARD_BALANCE_KEY,
        REFUND_METHOD_KEY,
    }
    for result in decided:
        assert result.evidence_ids
        assert set(result.evidence_ids) <= known
    assert not [r for r in results if r.status is ResultStatus.EVALUATOR_ERROR]


def test_a_violation_survives_an_invalid_run_and_a_pass_does_not_survive_an_early_end():
    results = evaluate_simulation(
        _subject(_steps(_cancel(), _read(), _cancel()), "infrastructure_error"),
        [StatusReadEvaluator()],
    ).results
    assert _statuses(results) == [FAIL, ResultStatus.INVALID]
    results = evaluate_simulation(
        _subject(_steps(_read(), _cancel()), "max_steps"), [StatusReadEvaluator()]
    ).results
    assert _statuses(results) == [UNKNOWN]


def test_a_state_source_that_raises_becomes_an_evaluator_error():
    results = evaluate_simulation(
        _subject(_steps(_cancel())), [OrderStatusEvaluator(CannedRetailState())]
    ).results
    assert _statuses(results) == [ResultStatus.EVALUATOR_ERROR]


# What the real tools accept, played through them so that the replay agrees


PENDING_ORDER, PENDING_OLD, PENDING_NEW = "#W5918442", "1725100896", "9007697085"
PENDING_METHOD = "credit_card_5051208"  # sofia_rossi_8776's only method
DELIVERED_ORDER, DELIVERED_OLD, DELIVERED_NEW = "#W4817420", "6777246137", "4579334072"
DELIVERED_METHOD = "gift_card_8168843"  # ava_moore_2033's only method
GIFT_CARD = "gift_card_7794233"  # olivia_ito_3591's, holding 56.00 in db.json
GIFT_PAID_ORDER = "#W3657213"  # 3570.34, paid with that gift card
OTHER_ORDER = "#W7941031"  # 2198.09, paid by credit card


def _played(*calls: tuple[str, dict]) -> tuple:
    """A conversation whose results come from the real tools on a fresh ``db.json``."""
    environment = get_environment()
    steps = []
    for number, (name, arguments) in enumerate(calls):
        response = environment.get_response(
            Tau2ToolCall(id=f"call-{number}", name=name, arguments=arguments)
        )
        steps.append((name, arguments, response.content, *((True,) if response.error else ())))
    return _steps(*steps)


@pytest.fixture(scope="module")
def replay() -> ReplayedRetailState:
    return ReplayedRetailState()


def _real(replay, messages):
    """Each check's results on a played conversation, whose replay must not diverge."""
    assert not replay.timeline(_simulation(messages)).divergences
    results = evaluate_simulation(_subject(messages), write_eligibility_evaluators(replay)).results
    return {
        key: [r for r in results if r.evaluation_key == key]
        for key in {r.evaluation_key for r in results}
    }


def _order_read(order_id: str) -> tuple[str, dict]:
    return ("get_order_details", {"order_id": order_id})


def _modify_pending() -> tuple[str, dict]:
    return (
        "modify_pending_order_items",
        {
            "order_id": PENDING_ORDER,
            "item_ids": [PENDING_OLD],
            "new_item_ids": [PENDING_NEW],
            "payment_method_id": PENDING_METHOD,
        },
    )


def _real_address(order_id: str) -> tuple[str, dict]:
    return (
        "modify_pending_order_address",
        {
            "order_id": order_id,
            "address1": "1 Main St",
            "address2": "",
            "city": "Austin",
            "state": "TX",
            "country": "USA",
            "zip": "78701",
        },
    )


def _real_exchange(new_item: str) -> tuple[str, dict]:
    return (
        "exchange_delivered_order_items",
        {
            "order_id": DELIVERED_ORDER,
            "item_ids": [DELIVERED_OLD],
            "new_item_ids": [new_item],
            "payment_method_id": DELIVERED_METHOD,
        },
    )


def _to_gift_card(order_id: str) -> tuple[str, dict]:
    return (
        "modify_pending_order_payment",
        {"order_id": order_id, "payment_method_id": GIFT_CARD},
    )


def test_a_write_on_pending_item_modified_fails_though_the_tool_accepts_it(replay):
    messages = _played(
        _order_read(PENDING_ORDER),
        _modify_pending(),
        _order_read(PENDING_ORDER),
        _real_address(PENDING_ORDER),
    )
    address = tool_timeline(messages)[-1]
    assert address.succeeded, "the tool should accept this write"
    checked = _real(replay, messages)
    assert _statuses(checked[ORDER_STATUS_KEY]) == [PASS, FAIL]
    assert "pending (item modified)" in checked[ORDER_STATUS_KEY][1].reason
    assert _statuses(checked[STATUS_READ_KEY]) == [PASS, PASS]


def test_an_exchange_to_the_identical_item_fails_though_the_tool_accepts_it(replay):
    messages = _played(_order_read(DELIVERED_ORDER), _real_exchange(DELIVERED_OLD))
    assert tool_timeline(messages)[-1].succeeded, "the tool should accept this write"
    checked = _real(replay, messages)
    assert _statuses(checked[ITEM_VARIANT_KEY]) == [FAIL]
    assert "identical" in checked[ITEM_VARIANT_KEY][0].reason
    assert _statuses(checked[ITEM_CHANGE_ONCE_KEY]) == [PASS]
    assert _statuses(checked[ORDER_STATUS_KEY]) == [PASS]


def test_a_second_item_change_is_caught_after_the_tool_accepted_the_first(replay):
    messages = _played(
        _order_read(DELIVERED_ORDER),
        _real_exchange(DELIVERED_OLD),
        _order_read(DELIVERED_ORDER),
        _real_exchange(DELIVERED_NEW),
    )
    first, second = tool_timeline(messages)[1], tool_timeline(messages)[3]
    assert first.succeeded
    assert not second.succeeded
    checked = _real(replay, messages)
    assert _statuses(checked[ITEM_CHANGE_ONCE_KEY]) == [PASS, FAIL]
    assert _statuses(checked[ORDER_STATUS_KEY]) == [PASS, FAIL]


def test_a_stale_read_fails_on_a_real_run(replay):
    messages = _played(
        _order_read(DELIVERED_ORDER),
        _real_exchange(DELIVERED_NEW),
        (
            "return_delivered_order_items",
            {
                "order_id": DELIVERED_ORDER,
                "item_ids": [DELIVERED_OLD],
                "payment_method_id": DELIVERED_METHOD,
            },
        ),
    )
    checked = _real(replay, messages)
    assert _statuses(checked[STATUS_READ_KEY]) == [PASS, FAIL]


def test_the_gift_card_balance_is_the_one_at_call_time_not_the_one_in_db_json(replay):
    messages = _played(
        _to_gift_card(OTHER_ORDER),  # 56.00 does not cover 2198.09
        ("cancel_pending_order", {"order_id": GIFT_PAID_ORDER, "reason": "ordered by mistake"}),
        _to_gift_card(OTHER_ORDER),  # the refund of 3570.34 now does
    )
    timeline = tool_timeline(messages)
    assert not timeline[0].succeeded
    assert timeline[2].succeeded
    checked = _real(replay, messages)
    results = checked[GIFT_CARD_BALANCE_KEY]
    assert _statuses(results) == [FAIL, PASS]
    assert "56.00" in results[0].reason
    assert "3626.34" in results[1].reason


# The prototype's remaining mutations of the Milestone 2 record


def _check_record(raw: dict, source) -> dict[str, list]:
    simulation = simulation_from_dict(raw, UTC_ZONE)
    subject = EvaluationSubject(simulation.simulation_id, simulation, _task_0_events(simulation))
    evaluators = (*write_eligibility_evaluators(source), UserAccessEvaluator(source))
    results = evaluate_simulation(subject, evaluators).results
    return {
        e.evaluation_key: [r for r in results if r.evaluation_key == e.evaluation_key]
        for e in evaluators
    }


def _only_flagged_by(checked: dict[str, list]) -> set[str]:
    return {key for key, results in checked.items() if _failed(results)}


@pytest.mark.parametrize("raw", [TASK_0, TASK_2], ids=["task-0", "task-2"])
def test_the_recorded_tasks_have_no_failure(replay, raw):
    checked = _check_record(raw, replay)
    assert _only_flagged_by(checked) == set()
    statuses = {s for results in checked.values() for s in _statuses(results)}
    assert statuses == {PASS, NOT_APPLICABLE}


def test_mutation_status_read_removed_is_flagged_by_the_status_read_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    read = next(m for m in raw["messages"] if "get_order_details" in str(m.get("tool_calls")))
    read_id = read["tool_calls"][0]["id"]
    raw["messages"] = [m for m in raw["messages"] if m is not read and m.get("id") != read_id]
    assert _only_flagged_by(_check_record(raw, replay)) == {STATUS_READ_KEY}


def test_mutation_exchange_to_the_identical_items_is_flagged_by_the_variant_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    arguments = _write_call(raw)["tool_calls"][0]["arguments"]
    arguments["new_item_ids"] = list(arguments["item_ids"])
    checked = _check_record(raw, replay)
    assert _only_flagged_by(checked) == {ITEM_VARIANT_KEY}
    reason = checked[ITEM_VARIANT_KEY][0].reason
    assert all(f"item {item}" in reason for item in arguments["item_ids"])


def test_mutation_write_on_another_users_order_is_flagged_by_access_and_status_read(replay):
    raw = copy.deepcopy(TASK_0)
    _write_call(raw)["tool_calls"][0]["arguments"]["order_id"] = "#W4817420"
    assert _only_flagged_by(_check_record(raw, replay)) == {USER_ACCESS_KEY, STATUS_READ_KEY}


def test_mutation_cancellation_of_a_delivered_order_is_flagged_by_the_status_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    write = _write_call(raw)["tool_calls"][0]
    order_id = write["arguments"]["order_id"]
    write["name"] = "cancel_pending_order"
    write["arguments"] = {"order_id": order_id, "reason": "no longer needed"}
    checked = _check_record(raw, replay)
    assert _only_flagged_by(checked) == {ORDER_STATUS_KEY}
    assert "'delivered'" in checked[ORDER_STATUS_KEY][0].reason


def test_mutation_refund_to_another_method_is_flagged_by_the_refund_check_only(replay):
    raw = copy.deepcopy(TASK_2)
    write = next(m for m in raw["messages"] if "return_delivered" in str(m.get("tool_calls")))
    write["tool_calls"][0]["arguments"]["payment_method_id"] = "paypal_0000000"
    assert _only_flagged_by(_check_record(raw, replay)) == {REFUND_METHOD_KEY}


def test_the_task_2_fixture_holds_no_answer_key():
    text = json.dumps(TASK_2)
    for field in ("raw_data", "reward_info", "evaluation_criteria", "user_scenario"):
        assert field not in text
