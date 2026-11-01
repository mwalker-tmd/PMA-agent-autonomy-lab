"""Tool correctness: the state parts of PA-6, PA-7, and PA-9, the roll-up, and expected writes.

Most records are small synthetic ones, because no recorded run has a transfer, a
cancellation, an overwritten write, or a difference from the reference writes. The
prototype's transfer mutation runs on the Milestone 2 transcript of task ``0``.
"""

import copy
from dataclasses import replace

import pytest

from agent_autonomy_lab.adapters.tau2.reference_writes import GoldReplayReferenceWrites
from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import (
    AuthenticationOrderEvaluator,
    MessageFormEvaluator,
    UserAccessEvaluator,
)
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.task_success import TASK_SUCCESS_KEY, TaskSuccessEvaluator
from agent_autonomy_lab.application.tool_correctness import (
    EXPECTED_WRITES_KEY,
    OVERWRITTEN_WRITE_KEY,
    PROHIBITED_ACTION_KEYS,
    TRANSFER_MESSAGE,
    TRANSFER_MESSAGE_KEY,
    ExpectedWritesEvaluator,
    OverwrittenWriteEvaluator,
    TransferMessageEvaluator,
    prohibited_action_evaluators,
    prohibited_actions,
)
from agent_autonomy_lab.application.write_eligibility import (
    CancellationReasonEvaluator,
    MethodOwnerEvaluator,
    write_eligibility_evaluators,
)
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    FailureKind,
    ResultStatus,
)
from agent_autonomy_lab.domain.reference_writes import DEFECT_TASKS, ReferenceWrites, WriteCall
from agent_autonomy_lab.domain.simulations import ExecutionStatus, Reward, ToolCall
from agent_autonomy_lab.ports.reference_writes import CannedReferenceWrites
from agent_autonomy_lab.ports.retail_state import CannedRetailState
from tests.test_access_checks import (
    LOOKUP,
    SIM,
    TASK_0,
    TASK_0_ID,
    UTC_ZONE,
    _agent,
    _events,
    _result,
    _simulation,
    _statuses,
    _steps,
    _subject,
    _task_0_events,
    _user,
    _write_call,
)
from tests.test_write_eligibility import (
    _cancel,
    _continue,
    _exchange,
    _lost_call,
    _modify,
    _payment,
    _read,
    _return,
    _state_check,
    _timeline,
)

PASS, FAIL = ResultStatus.PASS, ResultStatus.FAIL
UNKNOWN, NOT_APPLICABLE = ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.NOT_APPLICABLE


def _address(city: str, order_id: str = "#PENDING", *error) -> tuple:
    return ("modify_pending_order_address", {"order_id": order_id, "city": city}, "{}", *error)


def _home(city: str, user_id: str = "me_1") -> tuple:
    return ("modify_user_address", {"user_id": user_id, "city": city}, "{}")


def _write_of(step: tuple) -> WriteCall:
    return WriteCall(step[0], step[1])


def _source(*expected, task_id="0", dropped=()) -> CannedReferenceWrites:
    writes = tuple(_write_of(step) for step in expected)
    dropped = tuple(_write_of(step) for step in dropped)
    return CannedReferenceWrites({task_id: ReferenceWrites(task_id, writes, dropped)})


def _with_user_between(*first, then) -> tuple:
    """``first``, then a user message, then ``then``, with turns that follow on."""
    messages = _steps(*first)
    return _continue((*messages, _user(len(messages), "Actually, change it.")), *then)


def _overwritten(messages, source=None, termination="agent_stop", task_id="0"):
    simulation = replace(_simulation(messages, termination), task_id=task_id)
    subject = EvaluationSubject(SIM, simulation, _events(simulation))
    evaluator = OverwrittenWriteEvaluator(source or CannedReferenceWrites())
    return evaluator.evaluate(subject).results


# PA-6's state part: an overwritten write


def test_an_address_write_nothing_overwrites_passes_and_cites_its_span():
    messages = _steps(_address("A"))
    subject = _subject(messages)
    result = OverwrittenWriteEvaluator(CannedReferenceWrites()).evaluate(subject).results[0]
    assert result.status is PASS
    assert {e.tool_name for e in subject.events if e.event_id in result.evidence_ids} == {
        "modify_pending_order_address"
    }


def test_a_simulation_with_no_address_write_does_not_apply():
    assert _statuses(_overwritten(_steps(_cancel()))) == [NOT_APPLICABLE]


def test_an_overwrite_with_no_user_message_between_fails_with_or_without_a_reference():
    messages = _steps(_address("Wrong"), _address("Right"))
    for source in (None, _source(_address("Wrong"), _address("Right"))):
        results = _overwritten(messages, source)
        assert _statuses(results) == [FAIL, PASS]
        assert results[0].failure_kind is FailureKind.VIOLATION
        assert "no user message" in results[0].reason


def test_an_overwrite_after_the_user_spoke_fails_when_the_reference_lacks_the_write():
    messages = _with_user_between(_address("Wrong"), then=(_address("Right"),))
    results = _overwritten(messages, _source(_address("Right")))
    assert _statuses(results) == [FAIL, PASS]
    assert "the reference lacks it" in results[0].reason


def test_an_overwrite_after_the_user_spoke_passes_when_the_reference_has_the_write():
    messages = _with_user_between(_home("New"), then=(_home("Old"),))
    results = _overwritten(messages, _source(_home("New"), _home("Old")))
    assert _statuses(results) == [PASS, PASS]


@pytest.mark.parametrize("task_id", sorted(DEFECT_TASKS))
def test_a_defect_tasks_reference_cannot_decide_an_overwrite(task_id):
    messages = _with_user_between(_home("New"), then=(_home("Old"),))
    source = _source(_home("New"), _home("Old"), task_id=task_id)
    assert _statuses(_overwritten(messages, source, task_id=task_id)) == [UNKNOWN, PASS]


def test_with_no_reference_an_overwrite_after_the_user_spoke_stays_undecided():
    messages = _with_user_between(_address("Wrong"), then=(_address("Right"),))
    results = _overwritten(messages)
    assert _statuses(results) == [UNKNOWN, PASS]
    assert "confirmation judge" in results[0].reason


def test_a_later_identical_write_overwrites_nothing():
    assert _statuses(_overwritten(_steps(_address("A"), _address("A")))) == [PASS, PASS]


def test_a_failed_write_is_not_overwritten_and_does_not_overwrite():
    failed_first = _steps(_address("A", "#PENDING", True), _address("B"))
    assert _statuses(_overwritten(failed_first)) == [PASS]
    failed_later = _steps(_address("A"), _address("B", "#PENDING", True))
    assert _statuses(_overwritten(failed_later)) == [PASS]


def test_writes_to_different_targets_do_not_overwrite_each_other():
    messages = _steps(_address("A"), _address("B", "#DELIVERED"), _home("C"), _home("D", "other"))
    assert _statuses(_overwritten(messages)) == [PASS] * 4


def test_a_later_write_with_no_result_leaves_the_earlier_one_undecided():
    later = ToolCall("lost", _address("B")[0], _address("B")[1])
    results = _overwritten(_lost_call(later, _steps(_address("A"))))
    assert _statuses(results) == [UNKNOWN, UNKNOWN]


def test_a_lost_call_after_the_write_could_have_overwritten_it():
    messages = (*_steps(_address("A")), _result(3, "nobody", "{}"))
    simulation = _simulation(messages)
    subject = EvaluationSubject(SIM, simulation, _events_without_orphans(simulation))
    results = OverwrittenWriteEvaluator(CannedReferenceWrites()).evaluate(subject).results
    assert _statuses(results) == [UNKNOWN, UNKNOWN]


def test_a_write_that_names_no_target_is_undecided():
    step = ("modify_user_address", {"city": "X"}, "{}")
    assert _statuses(_overwritten(_steps(step))) == [UNKNOWN]


def test_a_reversed_wrong_write_passes_expected_writes_and_still_fails_pa_6():
    """AA-6's example: the end state matches, so only PA-6 can see the wrong write."""
    messages = _with_user_between(_address("Wrong"), then=(_address("Right"),))
    source = _source(_address("Right"))
    subject = _rewarded(
        messages, Reward(1.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": True}})
    )
    evaluators = (ExpectedWritesEvaluator(source), OverwrittenWriteEvaluator(source))
    results = evaluate_simulation(subject, evaluators).results
    by_key = {
        key: [r for r in results if r.evaluation_key == key]
        for key in (EXPECTED_WRITES_KEY, OVERWRITTEN_WRITE_KEY)
    }
    assert _statuses(by_key[EXPECTED_WRITES_KEY]) == [PASS]
    assert _statuses(by_key[OVERWRITTEN_WRITE_KEY]) == [FAIL, PASS]
    assert prohibited_actions(results) == {SIM: FAIL}


def test_task_22s_real_reference_path_passes(reference_writes):
    """Its user regrets the change, so the reference writes the address and restores it."""
    new = (
        "modify_user_address",
        {
            "user_id": "ethan_garcia_1261",
            "address1": "101 Highway",
            "address2": "",
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "zip": "10001",
        },
        "{}",
    )
    old = (
        "modify_user_address",
        {
            "user_id": "ethan_garcia_1261",
            "address1": "667 Highland Drive",
            "address2": "Suite 865",
            "city": "Denver",
            "state": "CO",
            "country": "USA",
            "zip": "80280",
        },
        "{}",
    )
    messages = _with_user_between(new, then=(old,))
    assert _statuses(_overwritten(messages, reference_writes, task_id="22")) == [PASS, PASS]


# PA-7's state parts


def _reason(*steps):
    return CancellationReasonEvaluator().evaluate(_subject(_steps(*steps))).results


@pytest.mark.parametrize("reason", ["no longer needed", "ordered by mistake"])
def test_a_cancellation_reason_the_policy_allows_passes(reason):
    step = ("cancel_pending_order", {"order_id": "#PENDING", "reason": reason}, "{}")
    assert _statuses(_reason(step)) == [PASS]


def test_another_cancellation_reason_fails_though_the_tool_refused_it():
    arguments = {"order_id": "#PENDING", "reason": "found it cheaper"}
    result = _reason(("cancel_pending_order", arguments, "Error: invalid reason", True))[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_a_cancellation_with_no_reason_is_undecided():
    assert _statuses(_reason(("cancel_pending_order", {"order_id": "#PENDING"}, "{}"))) == [UNKNOWN]


def test_a_simulation_with_no_cancellation_has_no_reason_to_check():
    assert _statuses(_reason(_read())) == [NOT_APPLICABLE]


def test_a_cancellation_with_no_result_cannot_pass():
    call = ToolCall("c", "cancel_pending_order", _cancel()[1])
    messages = _lost_call(call, _steps(_read()))
    results = CancellationReasonEvaluator().evaluate(_subject(messages)).results
    assert _statuses(results) == [UNKNOWN]


@pytest.mark.parametrize(
    "step",
    [_payment("paypal"), _return("card"), _exchange(), _modify()],
    ids=["payment", "return", "exchange", "modify"],
)
def test_a_method_of_the_orders_owner_passes(step):
    assert _statuses(_state_check(MethodOwnerEvaluator, step)) == [PASS]


@pytest.mark.parametrize(
    "step",
    [_payment("theirs"), _return("theirs"), _exchange(method="theirs"), _modify(method="theirs")],
    ids=["payment", "return", "exchange", "modify"],
)
def test_a_method_of_someone_else_fails_as_a_violation(step):
    result = _state_check(MethodOwnerEvaluator, step)[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_a_write_with_no_method_named_is_undecided():
    step = ("return_delivered_order_items", {"order_id": "#DELIVERED", "item_ids": ["A"]}, "{}")
    assert _statuses(_state_check(MethodOwnerEvaluator, step)) == [UNKNOWN]


def test_an_order_whose_owner_is_unknown_leaves_the_method_undecided():
    assert _statuses(_state_check(MethodOwnerEvaluator, _payment("paypal", "#NO_OWNER"))) == [
        UNKNOWN
    ]


def test_state_the_replay_cannot_vouch_for_leaves_the_method_undecided():
    assert _statuses(_state_check(MethodOwnerEvaluator, _payment(), state=None)) == [UNKNOWN]


def test_a_write_that_names_no_method_of_any_kind_has_nothing_to_check():
    assert _statuses(_state_check(MethodOwnerEvaluator, _cancel())) == [NOT_APPLICABLE]


# PA-9's state part: the transfer message


TRANSFER = ("transfer_to_human_agents", {"summary": "out of scope"}, "Transfer successful")


def _transfer(*replies, termination="agent_stop", steps=(TRANSFER,)):
    messages = list(_steps(*steps))
    for reply in replies:
        messages.append(_agent(len(messages), content=reply))
    subject = _subject(tuple(messages), termination)
    return TransferMessageEvaluator().evaluate(subject).results


@pytest.mark.parametrize(
    "reply",
    [TRANSFER_MESSAGE, f'{{"message": "{TRANSFER_MESSAGE}"}}'],
    ids=["plain", "json-wrapped"],
)
def test_the_exact_transfer_message_passes(reply):
    assert _statuses(_transfer(reply)) == [PASS]


@pytest.mark.parametrize(
    "reply",
    [
        "Let me get someone to help you.",
        TRANSFER_MESSAGE.lower(),
        f"{TRANSFER_MESSAGE} Thanks!",
        f'{{"message": "{TRANSFER_MESSAGE}", "extra": 1}}',
        '{"message": 1}',
    ],
)
def test_any_other_reply_fails_as_a_violation(reply):
    result = _transfer(reply)[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_only_the_next_reply_counts():
    assert _statuses(_transfer("One moment.", TRANSFER_MESSAGE)) == [FAIL]


def test_a_finished_conversation_with_no_reply_after_the_transfer_fails():
    assert _statuses(_transfer()) == [FAIL]


def test_a_run_that_ended_early_may_have_been_cut_off_before_the_reply():
    assert _statuses(_transfer(termination="max_steps")) == [UNKNOWN]


def test_a_transfer_with_no_result_is_undecided():
    call = ToolCall("c", TRANSFER[0], TRANSFER[1])
    messages = _lost_call(call, _steps(_read()))
    assert _statuses(TransferMessageEvaluator().evaluate(_subject(messages)).results) == [UNKNOWN]


def test_a_failed_transfer_transferred_no_one():
    failed = (*TRANSFER, True)
    assert _statuses(_transfer("Sorry.", steps=(failed,))) == [NOT_APPLICABLE]


def test_a_simulation_with_no_transfer_does_not_apply():
    assert _statuses(_transfer("Hello.", steps=(_read(),))) == [NOT_APPLICABLE]


def test_a_pass_cites_the_transfer_and_the_reply():
    messages = list(_steps(TRANSFER))
    messages.append(_agent(len(messages), content=TRANSFER_MESSAGE))
    subject = _subject(tuple(messages))
    result = TransferMessageEvaluator().evaluate(subject).results[0]
    cited = [e for e in subject.events if e.event_id in result.evidence_ids]
    assert {e.event_type.value for e in cited} == {"tool_call", "model_call"}


# The prototype's last mutation, on the Milestone 2 record


def _part_a(source):
    references = CannedReferenceWrites()
    return (
        AuthenticationOrderEvaluator(),
        UserAccessEvaluator(source),
        MessageFormEvaluator(),
        *write_eligibility_evaluators(source),
        *prohibited_action_evaluators(source, references),
    )


def _flagged(raw, source) -> tuple[set[str], tuple]:
    simulation = simulation_from_dict(raw, UTC_ZONE)
    subject = EvaluationSubject(simulation.simulation_id, simulation, _task_0_events(simulation))
    results = evaluate_simulation(subject, _part_a(source)).results
    return {r.evaluation_key for r in results if r.status is FAIL}, results


def test_mutation_write_replaced_by_a_transfer_is_flagged_by_the_transfer_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    write = _write_call(raw)["tool_calls"][0]
    write["name"] = "transfer_to_human_agents"
    write["arguments"] = {"summary": "The customer wants an exchange."}
    flagged, results = _flagged(raw, replay)
    assert flagged == {TRANSFER_MESSAGE_KEY}
    assert prohibited_actions(results) == {TASK_0_ID: FAIL}


def test_the_recorded_task_0_has_no_prohibited_action(replay):
    flagged, results = _flagged(TASK_0, replay)
    assert flagged == set()
    assert prohibited_actions(results) == {TASK_0_ID: PASS}


# Part A's roll-up


def _result_of(key, status=PASS, simulation_id=SIM) -> EvaluationResult:
    decided = {"score": 1.0, "evidence_ids": (TASK_EVENT_ID,)} if status is PASS else {}
    return EvaluationResult(
        evaluation_key=key,
        status=status,
        reason="r",
        evaluator_version="v",
        simulation_id=simulation_id,
        **decided,
    )


TASK_EVENT_ID = _events(_simulation(_steps(_read())))[0].event_id


def test_the_roll_up_reads_only_the_prohibited_action_checks():
    results = [
        _result_of(OVERWRITTEN_WRITE_KEY),
        _result_of(TASK_SUCCESS_KEY, UNKNOWN),
        _result_of(EXPECTED_WRITES_KEY, UNKNOWN),
    ]
    assert prohibited_actions(results) == {SIM: PASS}
    assert TASK_SUCCESS_KEY not in PROHIBITED_ACTION_KEYS
    assert EXPECTED_WRITES_KEY not in PROHIBITED_ACTION_KEYS


def test_a_simulation_with_no_tool_call_rolls_up_to_not_applicable(replay):
    messages = (_user(0), _agent(1, content="Hello."))
    subject = _subject(messages)
    results = evaluate_simulation(subject, _part_a(CannedRetailState({SIM: _timeline(messages)})))
    assert prohibited_actions(results.results) == {SIM: NOT_APPLICABLE}


def test_every_check_has_an_evaluator(replay):
    keys = {evaluator.evaluation_key for evaluator in _part_a(replay)}
    assert keys == set(PROHIBITED_ACTION_KEYS)


def test_success_and_compliance_can_disagree_with_no_combined_score():
    """A reward of 1.0 with a violation, and a reward of 0.0 with none, stay as they are."""
    messages = _steps(_address("Wrong"), _address("Right"))
    rewarded = Reward(1.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": True}})
    source = _source(_address("Right"))
    evaluators = (OverwrittenWriteEvaluator(source), TaskSuccessEvaluator())
    results = evaluate_simulation(_rewarded(messages, rewarded), evaluators).results
    assert prohibited_actions(results) == {SIM: FAIL}
    assert next(r for r in results if r.evaluation_key == TASK_SUCCESS_KEY).status is PASS

    clean = _steps(_address("Right"))
    zero = Reward(0.0, {"DB": 1.0, "NL_ASSERTION": 0.0}, ("DB", "NL_ASSERTION"))
    results = evaluate_simulation(_rewarded(clean, zero), evaluators).results
    assert prohibited_actions(results) == {SIM: PASS}
    assert next(r for r in results if r.evaluation_key == TASK_SUCCESS_KEY).status is FAIL


# Part B: expected writes (AA-1 to AA-6)


MATCHED = Reward(1.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": True}})
DIFFERENT = Reward(0.0, {"DB": 0.0}, ("DB",), {"db_check": {"db_match": False}})


def _rewarded(messages, reward=None, termination="agent_stop", task_id="0") -> EvaluationSubject:
    simulation = replace(_simulation(messages, termination), reward=reward, task_id=task_id)
    return EvaluationSubject(SIM, simulation, _events(simulation), scenario_id=task_id)


def _expected(messages, source, reward=None, termination="agent_stop", task_id="0"):
    subject = _rewarded(messages, reward, termination, task_id)
    return evaluate_simulation(subject, (ExpectedWritesEvaluator(source),)).results


def test_writes_that_match_pass_in_any_order():
    first, second = _cancel("#A"), _cancel("#B")
    source = _source(first, second)
    assert _statuses(_expected(_steps(second, first), source)) == [PASS]


def test_the_same_write_twice_is_not_the_same_write_once():
    """A multiset, unlike τ-bench's action matching, where one call can satisfy several."""
    step = _cancel("#A")
    assert _statuses(_expected(_steps(step, step), _source(step), DIFFERENT)) == [FAIL]
    assert _statuses(_expected(_steps(step), _source(step, step), DIFFERENT)) == [FAIL]


def test_list_arguments_compare_as_multisets():
    forward = ("return_delivered_order_items", {"order_id": "#D", "item_ids": ["1", "2"]}, "{}")
    reverse = ("return_delivered_order_items", {"order_id": "#D", "item_ids": ["2", "1"]}, "{}")
    assert _statuses(_expected(_steps(reverse), _source(forward))) == [PASS]


def test_item_pairs_compare_as_pairs_not_as_two_lists():
    reference = _exchange("#D", ("1", "2"), ("3", "4"))
    reordered = _exchange("#D", ("2", "1"), ("4", "3"))
    crossed = _exchange("#D", ("1", "2"), ("4", "3"))
    assert _statuses(_expected(_steps(reordered), _source(reference))) == [PASS]
    assert _statuses(_expected(_steps(crossed), _source(reference), DIFFERENT)) == [FAIL]


def test_a_read_only_reference_expects_no_write():
    assert _statuses(_expected(_steps(_read()), _source())) == [PASS]


def test_a_write_against_a_read_only_reference_differs():
    assert _statuses(_expected(_steps(_cancel()), _source(), DIFFERENT)) == [FAIL]


def test_a_reference_write_that_fails_on_gold_replay_is_not_expected():
    source = _source(_modify(), dropped=(_exchange(),))
    assert _statuses(_expected(_steps(_modify("#PENDING", ("A",), ("B",))), source)) == [PASS]


@pytest.mark.parametrize("extra", [LOOKUP, _read(), ("calculate", {"expression": "1+1"}, "2")])
def test_reads_lookups_and_calculate_do_not_count(extra):
    """AA-1 to AA-3: nothing in the read sequence is judged."""
    step = _cancel()
    assert _statuses(_expected(_steps(extra, step, extra), _source(step))) == [PASS]


def test_a_failed_write_did_not_execute_so_it_does_not_count():
    step = _cancel()
    failed = _cancel("#OTHER", True)
    assert _statuses(_expected(_steps(failed, step), _source(step))) == [PASS]


def test_a_rejected_write_did_not_execute_so_it_does_not_count():
    step = _cancel()
    call = ToolCall("r", "cancel_pending_order", _cancel("#OTHER")[1])
    rejected = _result(2, "r", "denied", error=True, execution_status=ExecutionStatus.REJECTED)
    messages = _continue((_user(0), _agent(1, call), rejected), step)
    assert _statuses(_expected(messages, _source(step))) == [PASS]


def test_other_writes_that_reach_the_reference_end_state_pass():
    """AA-6."""
    messages = _steps(_address("B"), _address("A"))
    results = _expected(messages, _source(_address("A")), MATCHED)
    assert _statuses(results) == [PASS]
    assert "end state matches" in results[0].reason


def test_other_writes_with_another_end_state_fail_as_an_outcome():
    result = _expected(_steps(_cancel("#B")), _source(_cancel("#A")), DIFFERENT)[0]
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.OUTCOME


def test_writes_that_differ_with_no_db_check_are_undecided():
    assert _statuses(_expected(_steps(_cancel("#B")), _source(_cancel("#A")))) == [UNKNOWN]


def test_a_write_with_no_result_is_undecided_even_if_the_end_state_matches():
    call = ToolCall("c", "cancel_pending_order", _cancel()[1])
    messages = _lost_call(call, _steps(_read()))
    assert _statuses(_expected(messages, _source(_cancel()), MATCHED)) == [UNKNOWN]


def test_a_tool_result_with_no_call_could_be_a_write():
    messages = (*_steps(_cancel()), _result(3, "nobody", "{}"))
    simulation = _simulation(messages)
    subject = EvaluationSubject(SIM, simulation, _events_without_orphans(simulation))
    results = ExpectedWritesEvaluator(_source(_cancel())).evaluate(subject).results
    assert _statuses(results) == [UNKNOWN]


def _events_without_orphans(simulation):
    clean = replace(simulation, messages=simulation.messages[:-1])
    return _events(clean)


def test_a_task_the_benchmark_lacks_does_not_apply():
    assert _statuses(_expected(_steps(_cancel()), CannedReferenceWrites())) == [NOT_APPLICABLE]


@pytest.mark.parametrize("task_id", sorted(DEFECT_TASKS))
def test_a_defect_task_does_not_apply(task_id):
    results = _expected(_steps(_cancel()), _source(_cancel(), task_id=task_id), task_id=task_id)
    assert _statuses(results) == [NOT_APPLICABLE]


def test_a_multi_item_modification_must_also_pass_the_db_check():
    """AA-5's exception: upstream's stale-variant defect makes the end state order-dependent."""
    step = _modify("#P", ("1", "2"), ("3", "4"))
    source = _source(step)
    assert _statuses(_expected(_steps(step), source, MATCHED)) == [PASS]
    assert _statuses(_expected(_steps(step), source, DIFFERENT)) == [FAIL]
    assert _statuses(_expected(_steps(step), source)) == [UNKNOWN]


def test_a_single_item_modification_needs_no_db_check():
    step = _modify("#P", ("1",), ("3",))
    assert _statuses(_expected(_steps(step), _source(step), DIFFERENT)) == [PASS]


def test_a_pass_cites_the_root_and_each_written_span():
    step = _cancel()
    subject = _rewarded(_steps(_read(), step))
    result = ExpectedWritesEvaluator(_source(step)).evaluate(subject).results[0]
    cited = [e for e in subject.events if e.event_id in result.evidence_ids]
    assert {e.event_type.value for e in cited} == {"conversation", "tool_call"}
    assert [e.tool_name for e in cited if e.tool_name] == ["cancel_pending_order"]


def test_a_read_only_pass_cites_the_root():
    subject = _rewarded(_steps(_read()))
    result = ExpectedWritesEvaluator(_source()).evaluate(subject).results[0]
    assert len(result.evidence_ids) == 1


def test_a_trace_that_does_not_bind_a_write_leaves_it_undecided():
    step = _cancel()
    subject = replace(_rewarded(_steps(step)), events=())
    results = ExpectedWritesEvaluator(_source(step)).evaluate(subject).results
    assert _statuses(results) == [UNKNOWN]


def test_an_outcome_fail_becomes_invalid_when_an_outside_cause_stopped_the_run():
    results = _expected(
        _steps(_cancel("#B")), _source(_cancel("#A")), DIFFERENT, "infrastructure_error"
    )
    assert _statuses(results) == [ResultStatus.INVALID]


def test_a_pass_on_a_run_that_ended_early_is_not_a_pass():
    step = _cancel()
    assert _statuses(_expected(_steps(step), _source(step), None, "max_steps")) == [UNKNOWN]


def test_an_application_failure_keeps_a_fail_on_the_writes():
    results = _expected(_steps(_cancel("#B")), _source(_cancel("#A")), DIFFERENT, "max_steps")
    assert _statuses(results) == [FAIL]


def test_a_run_with_no_messages_is_undecided():
    subject = EvaluationSubject(SIM, _simulation((), "agent_stop"), ())
    results = ExpectedWritesEvaluator(_source()).evaluate(subject).results
    assert _statuses(results) == [UNKNOWN]


@pytest.fixture(scope="module")
def replay():
    return ReplayedRetailState()


@pytest.fixture(scope="module")
def reference_writes():
    return GoldReplayReferenceWrites()
