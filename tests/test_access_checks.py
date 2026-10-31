"""The authentication, access, and message-form checks, on fixtures.

Task ``0``'s record from the Milestone 2 run of record is the positive case and
the source of the prototype's mutations (docs/BASELINE.md, "What the baseline run
showed"). It holds the transcript only, with no answer key. The small synthetic
records below cover the shapes no recorded run has.
"""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from tau2.domains.retail.environment import get_environment

from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import (
    AUTHENTICATION_ORDER_KEY,
    MESSAGE_FORM_KEY,
    USER_ACCESS_KEY,
    AuthenticationOrderEvaluator,
    MessageFormEvaluator,
    UserAccessEvaluator,
    authentication_compliance,
)
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.export_simulation import simulation_trace
from agent_autonomy_lab.domain.evaluation import EvaluationSubject, FailureKind, ResultStatus
from agent_autonomy_lab.domain.evaluation.trace_evidence import TraceEvidence
from agent_autonomy_lab.domain.events import EventType, ExperimentClass
from agent_autonomy_lab.domain.retail_state import (
    ReplayDivergence,
    RetailState,
    RetailStateTimeline,
    WriteAttempt,
    WriteOutcome,
)
from agent_autonomy_lab.domain.simulations import (
    ExecutionStatus,
    Message,
    Role,
    SavedRun,
    SavedSimulation,
    ToolCall,
)
from agent_autonomy_lab.domain.tool_timeline import (
    AUTHENTICATION_TOOLS,
    KNOWN_TOOLS,
    PROTECTED_READS,
    WRITE_TOOLS,
    tool_timeline,
)
from agent_autonomy_lab.ports.retail_state import CannedRetailState, RetailStateSource
from tests.test_trace_live_run import SIM_ID
from tests.test_trace_live_run import simulation as live_simulation
from tests.test_trace_live_run import trace as live_trace_of_a_run

UTC_ZONE = ZoneInfo("UTC")
T0 = datetime(2026, 10, 31, tzinfo=UTC)
TASK_0 = json.loads(
    (Path(__file__).parent / "fixtures" / "m2-task-0" / "simulation.json").read_text("utf-8")
)
TASK_0_ID = TASK_0["id"]
TASK_0_USER = "yusuf_rossi_9620"
OTHER_DELIVERED_ORDER = "#W4817420"  # ava_moore_2033's, in db.json

ME = "me_1"
OTHER = "other_2"
SIM = "sim"
OWNERSHIP = RetailState(
    db_hash="h",
    orders={"#MINE": {"user_id": ME}, "#THEIRS": {"user_id": OTHER}, "#ORPHAN": {}},
    users={ME: {}, OTHER: {}},
    products={},
)

LOOKUP = ("find_user_id_by_email", {"email": "a@example.com"}, ME)
FAILED_LOOKUP = ("find_user_id_by_email", {"email": "b@example.com"}, "Error: User not found")
READ_MINE = ("get_order_details", {"order_id": "#MINE"}, "{}")
READ_THEIRS = ("get_order_details", {"order_id": "#THEIRS"}, "{}")
CANCEL = ("cancel_pending_order", {"order_id": "#MINE", "reason": "no longer needed"}, "{}")
CATALOG = ("list_all_product_types", {}, "{}")


def _call_id(index: int) -> str:
    return f"call-{index}"


def _result(index: int, call_id: str, content: str | None, **fields) -> Message:
    fields.setdefault("execution_status", ExecutionStatus.EXECUTED)
    return Message(
        role=Role.TOOL,
        turn_index=index,
        timestamp=T0,
        tool_call_id=call_id,
        content=content,
        **fields,
    )


def _agent(index: int, *calls: ToolCall, content: str | None = None) -> Message:
    return Message(
        role=Role.ASSISTANT,
        turn_index=index,
        timestamp=T0,
        tool_calls=calls,
        content=content,
        cost=0.001,
    )


def _user(index: int, content: str = "Hello") -> Message:
    return Message(role=Role.USER, turn_index=index, timestamp=T0, content=content)


def _steps(*steps: tuple) -> tuple[Message, ...]:
    """A conversation of (tool, arguments, result) steps; ``error=True`` as a fourth item."""
    messages = [_user(0)]
    for number, step in enumerate(steps):
        name, arguments, content, *rest = step
        call_id = _call_id(number)
        messages.append(_agent(len(messages), ToolCall(call_id, name, arguments)))
        messages.append(_result(len(messages), call_id, content, error=bool(rest)))
    return tuple(messages)


def _simulation(messages, termination_reason="agent_stop") -> SavedSimulation:
    return SavedSimulation(SIM, "0", T0, 1.0, termination_reason, tuple(messages))


def _events(simulation: SavedSimulation):
    run = SavedRun("run", None, None, None, None, (simulation,))
    return tuple(simulation_trace(run, simulation, ExperimentClass.CUSTOM_LAB_EXPERIMENT)[0])


def _subject(messages, termination_reason="agent_stop", events=None) -> EvaluationSubject:
    simulation = _simulation(messages, termination_reason)
    return EvaluationSubject(
        simulation_id=SIM,
        simulation=simulation,
        events=_events(simulation) if events is None else events,
    )


def _timeline(state: RetailState | None = OWNERSHIP) -> RetailStateTimeline:
    if state is None:
        return RetailStateTimeline(
            SIM, (), None, "h", divergences=(ReplayDivergence("c", "unknown", "lost"),)
        )
    return RetailStateTimeline(SIM, (), state, state.db_hash)


def _access(state: RetailState | None = OWNERSHIP) -> UserAccessEvaluator:
    return UserAccessEvaluator(CannedRetailState({SIM: _timeline(state)}))


def _order_results(*steps):
    return AuthenticationOrderEvaluator().evaluate(_subject(_steps(*steps))).results


def _access_results(*steps, state=OWNERSHIP):
    return _access(state).evaluate(_subject(_steps(*steps))).results


def _statuses(results) -> list[ResultStatus]:
    return [result.status for result in results]


# Authentication order (lines 10 and 12, PA-1)


def test_a_protected_operation_after_a_successful_lookup_passes():
    results = _order_results(LOOKUP, READ_MINE, CANCEL)
    assert _statuses(results) == [ResultStatus.PASS, ResultStatus.PASS]


def test_a_failed_lookup_before_a_successful_one_is_allowed():
    results = _order_results((*FAILED_LOOKUP, True), LOOKUP, READ_MINE)
    assert _statuses(results) == [ResultStatus.PASS]


@pytest.mark.parametrize("operation", [READ_MINE, CANCEL])
def test_a_protected_operation_before_any_lookup_fails_as_a_violation(operation):
    result = _order_results(operation, LOOKUP)[0]
    assert result.status is ResultStatus.FAIL
    assert result.failure_kind is FailureKind.VIOLATION


def test_a_failed_lookup_does_not_authenticate():
    results = _order_results((*FAILED_LOOKUP, True), READ_MINE)
    assert _statuses(results) == [ResultStatus.FAIL]


def test_an_attempt_counts_even_when_the_tool_failed():
    results = _order_results(("get_order_details", {"order_id": "#MINE"}, "Error: x", True))
    assert _statuses(results) == [ResultStatus.FAIL]


@pytest.mark.parametrize(
    "catalog_read",
    [
        CATALOG,
        ("get_product_details", {"product_id": "1"}, "{}"),
        ("get_item_details", {"item_id": "1"}, "{}"),
        ("calculate", {"expression": "1+1"}, "2"),
        ("transfer_to_human_agents", {"summary": "s"}, "Transfer successful"),
    ],
)
def test_an_unprotected_call_before_authentication_does_not_fail_line_12(catalog_read):
    assert _statuses(_order_results(catalog_read)) == [ResultStatus.NOT_APPLICABLE]
    assert _statuses(_order_results(catalog_read, LOOKUP, READ_MINE)) == [ResultStatus.PASS]


def test_a_protected_operation_with_no_result_is_insufficient_after_a_lookup():
    messages = _steps(LOOKUP)
    call = ToolCall("lost", "get_order_details", {"order_id": "#MINE"})
    results = (
        AuthenticationOrderEvaluator()
        .evaluate(_subject((*messages, _agent(len(messages), call))))
        .results
    )
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_a_protected_operation_with_no_result_is_still_a_violation_before_any_lookup():
    call = ToolCall("lost", "get_order_details", {"order_id": "#MINE"})
    results = AuthenticationOrderEvaluator().evaluate(_subject((_user(0), _agent(1, call)))).results
    assert _statuses(results) == [ResultStatus.FAIL]


def test_a_lookup_with_no_result_leaves_what_follows_undecided():
    lookup = ToolCall("lost", "find_user_id_by_email", {"email": "a@example.com"})
    read = ToolCall("read", "get_order_details", {"order_id": "#MINE"})
    messages = (_user(0), _agent(1, lookup), _agent(2, read), _result(3, "read", "{}"))
    results = AuthenticationOrderEvaluator().evaluate(_subject(messages)).results
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE] * 2


def test_a_result_with_no_call_could_be_a_lost_lookup_so_no_violation_is_claimed():
    read = ToolCall("read", "get_order_details", {"order_id": "#MINE"})
    messages = (
        _user(0),
        _result(1, "lost", ME),
        _agent(2, read),
        _result(3, "read", "{}"),
    )
    simulation = _simulation(messages)
    subject = EvaluationSubject(SIM, simulation, ())
    results = AuthenticationOrderEvaluator().evaluate(subject).results
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE] * 2


@pytest.mark.parametrize(
    "fields",
    [
        {"execution_status": None},
        {"execution_status": ExecutionStatus.REJECTED},
    ],
)
def test_a_lookup_whose_record_contradicts_itself_authenticates_no_one(fields):
    lookup = ToolCall("find", "find_user_id_by_email", {"email": "a@example.com"})
    read = ToolCall("read", "get_order_details", {"order_id": "#MINE"})
    messages = (
        _user(0),
        _agent(1, lookup),
        _result(2, "find", ME, **fields),
        _agent(3, read),
        _result(4, "read", "{}"),
    )
    results = AuthenticationOrderEvaluator().evaluate(_subject(messages)).results
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE] * 2


@pytest.mark.parametrize("content", [None, "", "  ", "two words"])
def test_a_lookup_that_returned_no_usable_id_authenticates_no_one(content):
    results = _order_results(("find_user_id_by_email", {"email": "a"}, content), READ_MINE)
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE] * 2


def test_a_rejected_lookup_with_an_error_is_a_failed_lookup_not_an_authentication():
    lookup = ToolCall("find", "find_user_id_by_email", {"email": "a@example.com"})
    read = ToolCall("read", "get_order_details", {"order_id": "#MINE"})
    messages = (
        _user(0),
        _agent(1, lookup),
        _result(2, "find", ME, error=True, execution_status=ExecutionStatus.REJECTED),
        _agent(3, read),
        _result(4, "read", "{}"),
    )
    results = AuthenticationOrderEvaluator().evaluate(_subject(messages)).results
    assert _statuses(results) == [ResultStatus.FAIL]


def test_no_protected_operation_is_not_applicable():
    results = _order_results(LOOKUP)
    assert _statuses(results) == [ResultStatus.NOT_APPLICABLE]
    assert results[0].target is None


def test_a_trace_with_no_spans_cannot_support_a_pass_or_a_fail():
    evaluator = AuthenticationOrderEvaluator()
    passing = evaluator.evaluate(_subject(_steps(LOOKUP, READ_MINE), events=())).results
    failing = evaluator.evaluate(_subject(_steps(READ_MINE), events=())).results
    assert _statuses(passing) == [ResultStatus.INSUFFICIENT_EVIDENCE]
    assert _statuses(failing) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_a_span_that_disagrees_with_the_call_is_not_evidence():
    messages = _steps(LOOKUP, READ_MINE)
    other = _simulation(_steps(LOOKUP, READ_THEIRS))
    results = AuthenticationOrderEvaluator().evaluate(_subject(messages, events=_events(other)))
    assert _statuses(results.results) == [ResultStatus.INSUFFICIENT_EVIDENCE]


@pytest.mark.parametrize(
    "evaluator", [AuthenticationOrderEvaluator(), _access(), MessageFormEvaluator()]
)
@pytest.mark.parametrize(
    "subject",
    [
        EvaluationSubject(SIM),
        EvaluationSubject(SIM, _simulation(())),
    ],
)
def test_a_run_with_no_messages_gives_insufficient_evidence(evaluator, subject):
    assert _statuses(evaluator.evaluate(subject).results) == [ResultStatus.INSUFFICIENT_EVIDENCE]


# User access (line 14, PA-3)


def test_targets_of_the_authenticated_user_pass():
    get_user = ("get_user_details", {"user_id": ME}, "{}")
    results = _access_results(LOOKUP, get_user, READ_MINE, CANCEL)
    assert _statuses(results) == [ResultStatus.PASS] * 3


@pytest.mark.parametrize(
    "operation",
    [
        READ_THEIRS,
        ("get_user_details", {"user_id": OTHER}, "{}"),
        ("modify_user_address", {"user_id": OTHER, "address1": "x"}, "{}"),
        ("cancel_pending_order", {"order_id": "#THEIRS", "reason": "ordered by mistake"}, "{}"),
    ],
)
def test_a_target_of_another_user_fails_as_a_violation(operation):
    result = _access_results(LOOKUP, operation)[0]
    assert result.status is ResultStatus.FAIL
    assert result.failure_kind is FailureKind.VIOLATION
    assert OTHER in result.reason


def test_an_attempt_on_another_users_order_counts_even_when_the_tool_failed():
    results = _access_results(LOOKUP, (*READ_THEIRS[:2], "Error: x", True))
    assert _statuses(results) == [ResultStatus.FAIL]


def test_a_read_before_authentication_is_compared_with_the_user_who_authenticates_later():
    assert _statuses(_access_results(READ_THEIRS, LOOKUP)) == [ResultStatus.FAIL]
    assert _statuses(_access_results(READ_MINE, LOOKUP)) == [ResultStatus.PASS]


def test_two_lookups_returning_different_users_fail_even_with_no_protected_operation():
    second = ("find_user_id_by_email", {"email": "c@example.com"}, OTHER)
    result = _access_results(LOOKUP, second)[0]
    assert result.status is ResultStatus.FAIL
    assert len(result.evidence_ids) == 2


def test_two_lookups_returning_the_same_user_are_not_a_conflict():
    second = ("find_user_id_by_name_zip", {"first_name": "A"}, ME)
    assert _statuses(_access_results(LOOKUP, second)) == [ResultStatus.NOT_APPLICABLE]


@pytest.mark.parametrize(
    "operation",
    [
        ("get_order_details", {"order_id": "#NOPE"}, "Error: Order not found", True),
        ("get_user_details", {"user_id": "nobody"}, "Error: User not found", True),
        ("get_order_details", {"order_id": "#ORPHAN"}, "{}"),
        ("get_order_details", {"order_id": 7}, "Error", True),
        ("get_order_details", {"order_id": ["#MINE"]}, "Error", True),
        ("get_order_details", {}, "Error", True),
    ],
)
def test_a_target_with_no_known_owner_is_insufficient(operation):
    assert _statuses(_access_results(LOOKUP, operation)) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_an_operation_with_no_authenticated_user_is_insufficient_not_a_second_failure():
    assert _statuses(_access_results(READ_MINE)) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_no_state_leaves_an_order_undecided_but_not_the_users_own_id():
    get_user = ("get_user_details", {"user_id": ME}, "{}")
    results = _access_results(LOOKUP, READ_MINE, get_user, state=None)
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.PASS]


def test_a_divergence_after_a_vouched_state_keeps_ownership():
    timeline = RetailStateTimeline(
        SIM,
        (
            WriteAttempt("a", "cancel_pending_order", WriteOutcome.EXECUTED, OWNERSHIP),
            WriteAttempt("b", "cancel_pending_order", WriteOutcome.EXECUTED, None),
        ),
        None,
        "h",
        divergences=(ReplayDivergence("a", "cancel_pending_order", "differs"),),
    )
    source = CannedRetailState({SIM: timeline})
    results = UserAccessEvaluator(source).evaluate(_subject(_steps(LOOKUP, READ_THEIRS))).results
    assert _statuses(results) == [ResultStatus.FAIL]


def test_a_lookup_returning_a_user_the_database_lacks_is_insufficient():
    lookup = ("find_user_id_by_email", {"email": "a"}, "ghost")
    assert _statuses(_access_results(lookup, READ_MINE)) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_a_later_lookup_with_no_result_is_insufficient():
    messages = _steps(LOOKUP)
    call = ToolCall("lost", "find_user_id_by_email", {"email": "c@example.com"})
    subject = _subject((*messages, _agent(len(messages), call)))
    assert _statuses(_access().evaluate(subject).results) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_no_protected_operation_and_no_conflict_is_not_applicable():
    assert _statuses(_access_results(LOOKUP, CATALOG)) == [ResultStatus.NOT_APPLICABLE]


def test_a_state_source_that_raises_becomes_an_evaluator_error():
    results = evaluate_simulation(
        _subject(_steps(LOOKUP, READ_MINE)), [UserAccessEvaluator(CannedRetailState())]
    ).results
    assert _statuses(results) == [ResultStatus.EVALUATOR_ERROR]


# Message form (line 20, PA-8)


def _form(*messages):
    return MessageFormEvaluator().evaluate(_subject(messages)).results


def test_one_known_tool_per_message_with_no_reply_passes():
    assert _statuses(_form(*_steps(LOOKUP, READ_MINE))) == [ResultStatus.PASS] * 2


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_a_tool_call_with_no_reply_text_passes(content):
    call = ToolCall("a", "calculate", {"expression": "1"})
    messages = (_user(0), _agent(1, call, content=content), _result(2, "a", "1"))
    assert _statuses(_form(*messages)) == [ResultStatus.PASS]


def test_two_calls_in_one_message_fail():
    first = ToolCall("a", "calculate", {"expression": "1"})
    second = ToolCall("b", "calculate", {"expression": "2"})
    messages = (_user(0), _agent(1, first, second), _result(2, "a", "1"), _result(3, "b", "2"))
    result = _form(*messages)[0]
    assert result.status is ResultStatus.FAIL
    assert result.failure_kind is FailureKind.VIOLATION
    assert "2 tool calls" in result.reason


def test_a_reply_beside_a_tool_call_fails():
    call = ToolCall("a", "calculate", {"expression": "1"})
    messages = (_user(0), _agent(1, call, content="One moment."), _result(2, "a", "1"))
    assert _statuses(_form(*messages)) == [ResultStatus.FAIL]


def test_a_call_to_an_unknown_tool_fails():
    call = ToolCall("a", "delete_everything", {})
    messages = (_user(0), _agent(1, call), _result(2, "a", "Error: no such tool", error=True))
    result = _form(*messages)[0]
    assert result.status is ResultStatus.FAIL
    assert "delete_everything" in result.reason


def test_a_message_with_no_model_span_gives_insufficient_evidence():
    call = ToolCall("a", "calculate", {"expression": "1"})
    agent = Message(role=Role.ASSISTANT, turn_index=1, timestamp=T0, tool_calls=(call,))
    assert _statuses(_form(_user(0), agent, _result(2, "a", "1"))) == [
        ResultStatus.INSUFFICIENT_EVIDENCE
    ]


def test_a_result_with_no_call_gives_insufficient_evidence_for_message_form():
    subject = EvaluationSubject(SIM, _simulation((_user(0), _result(1, "lost", "x"))), ())
    assert _statuses(MessageFormEvaluator().evaluate(subject).results) == [
        ResultStatus.INSUFFICIENT_EVIDENCE
    ]


def test_no_tool_call_is_not_applicable():
    assert _statuses(_form(_user(0), _agent(1, content="Hi"))) == [ResultStatus.NOT_APPLICABLE]


# Every result, and the roll-up


def test_each_unit_has_one_result_with_a_distinct_target_even_with_no_spans():
    first = ToolCall("a", "get_order_details", {"order_id": "#MINE"})
    second = ToolCall("b", "get_order_details", {"order_id": "#MINE"})
    subject = _subject((_user(0), _agent(1, first, second)), events=())
    results = AuthenticationOrderEvaluator().evaluate(subject).results
    assert len({result.target for result in results}) == 2


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (READ_MINE, ResultStatus.PASS),
        (READ_THEIRS, ResultStatus.FAIL),
        (("get_order_details", {"order_id": "#NOPE"}, "Error", True), None),
    ],
)
def test_authentication_compliance_rolls_up_both_rules(operation, expected):
    evaluated = evaluate_simulation(
        _subject(_steps(LOOKUP, operation)), [AuthenticationOrderEvaluator(), _access()]
    )
    assert authentication_compliance(evaluated.results) == {
        SIM: expected or ResultStatus.INSUFFICIENT_EVIDENCE
    }


def test_authentication_compliance_reads_only_its_own_rules():
    evaluated = evaluate_simulation(
        _subject(_steps(LOOKUP, CATALOG)),
        [AuthenticationOrderEvaluator(), _access(), MessageFormEvaluator()],
    )
    assert authentication_compliance(evaluated.results) == {SIM: ResultStatus.NOT_APPLICABLE}


def test_a_violation_survives_an_invalid_run_and_a_pass_does_not_survive_an_early_end():
    subject = _subject(_steps(READ_MINE, LOOKUP, READ_MINE), "infrastructure_error")
    results = evaluate_simulation(subject, [AuthenticationOrderEvaluator()]).results
    assert _statuses(results) == [ResultStatus.FAIL, ResultStatus.INVALID]
    subject = _subject(_steps(LOOKUP, READ_MINE), "max_steps")
    results = evaluate_simulation(subject, [AuthenticationOrderEvaluator()]).results
    assert _statuses(results) == [ResultStatus.INSUFFICIENT_EVIDENCE]


def test_every_decided_result_cites_events_of_the_right_kind():
    subject = _subject(_steps(READ_THEIRS, LOOKUP, READ_MINE))
    results = evaluate_simulation(
        subject, [AuthenticationOrderEvaluator(), _access(), MessageFormEvaluator()]
    ).results
    by_id = {event.event_id: event for event in subject.events}
    decided = [r for r in results if r.status in (ResultStatus.PASS, ResultStatus.FAIL)]
    assert decided
    for result in decided:
        assert result.evidence_ids
        types = {by_id[event_id].event_type for event_id in result.evidence_ids}
        assert types <= {EventType.MODEL_CALL, EventType.TOOL_CALL}


def test_the_tool_lists_match_the_pinned_retail_toolkit():
    tools = get_environment().tools
    names = set(tools.get_tools())
    assert KNOWN_TOOLS == names
    assert WRITE_TOOLS == {name for name in names if tools.tool_mutates_state(name)}
    assert AUTHENTICATION_TOOLS | PROTECTED_READS <= names


def test_a_live_trace_supplies_the_same_evidence_as_an_export():
    events, _ = live_trace_of_a_run()
    record = live_simulation()
    record = replace(
        record,
        messages=tuple(
            replace(m, execution_status=ExecutionStatus.EXECUTED) if m.role is Role.TOOL else m
            for m in record.messages
        ),
    )
    subject = EvaluationSubject(SIM_ID, record, tuple(events))
    timeline = tool_timeline(record.messages)
    assert TraceEvidence(events).tool_span(timeline[0]) is not None
    results = evaluate_simulation(subject, [MessageFormEvaluator()]).results
    assert _statuses(results) == [ResultStatus.PASS]


# The Milestone 2 record, and the prototype's mutations of it


@pytest.fixture(scope="module")
def replay() -> RetailStateSource:
    return ReplayedRetailState()


def _check_task_0(raw: dict, source: RetailStateSource) -> dict[str, list]:
    simulation = simulation_from_dict(raw, UTC_ZONE)
    subject = EvaluationSubject(TASK_0_ID, simulation, _task_0_events(simulation))
    results = evaluate_simulation(
        subject,
        [AuthenticationOrderEvaluator(), UserAccessEvaluator(source), MessageFormEvaluator()],
    ).results
    return {
        key: [r for r in results if r.evaluation_key == key]
        for key in (AUTHENTICATION_ORDER_KEY, USER_ACCESS_KEY, MESSAGE_FORM_KEY)
    }


def _task_0_events(simulation: SavedSimulation):
    run = SavedRun("run", None, None, None, None, (simulation,))
    return tuple(simulation_trace(run, simulation, ExperimentClass.OFFICIAL_UPSTREAM_BASELINE)[0])


def _failed(results: list) -> list:
    return [result for result in results if result.status is ResultStatus.FAIL]


def _write_call(raw: dict) -> dict:
    return next(m for m in raw["messages"] if m.get("tool_calls") and "exchange" in str(m))


def test_the_recorded_task_0_passes_every_check(replay):
    checked = _check_task_0(TASK_0, replay)
    assert _statuses(checked[AUTHENTICATION_ORDER_KEY]) == [ResultStatus.PASS] * 3
    assert _statuses(checked[USER_ACCESS_KEY]) == [ResultStatus.PASS] * 3
    assert _statuses(checked[MESSAGE_FORM_KEY]) == [ResultStatus.PASS] * 6


def test_mutation_authentication_removed_is_flagged_by_the_authentication_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    lookup = next(m for m in raw["messages"] if "find_user_id" in str(m.get("tool_calls")))
    lookup_id = lookup["tool_calls"][0]["id"]
    raw["messages"] = [m for m in raw["messages"] if m is not lookup and m.get("id") != lookup_id]
    checked = _check_task_0(raw, replay)
    assert len(_failed(checked[AUTHENTICATION_ORDER_KEY])) == 3
    assert not _failed(checked[USER_ACCESS_KEY])
    assert not _failed(checked[MESSAGE_FORM_KEY])


def test_mutation_another_users_order_is_flagged_by_the_access_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    write = _write_call(raw)["tool_calls"][0]
    write["arguments"]["order_id"] = OTHER_DELIVERED_ORDER
    checked = _check_task_0(raw, replay)
    failed = _failed(checked[USER_ACCESS_KEY])
    assert len(failed) == 1
    assert "ava_moore_2033" in failed[0].reason
    assert not _failed(checked[AUTHENTICATION_ORDER_KEY])
    assert not _failed(checked[MESSAGE_FORM_KEY])


def test_mutation_reply_text_in_a_writes_message_is_flagged_by_the_form_check_only(replay):
    raw = copy.deepcopy(TASK_0)
    _write_call(raw)["content"] = "Submitting your exchange now."
    checked = _check_task_0(raw, replay)
    failed = _failed(checked[MESSAGE_FORM_KEY])
    assert len(failed) == 1
    assert "reply" in failed[0].reason
    assert not _failed(checked[AUTHENTICATION_ORDER_KEY])
    assert not _failed(checked[USER_ACCESS_KEY])


def test_the_fixture_holds_no_answer_key():
    text = json.dumps(TASK_0)
    for field in ("raw_data", "reward_info", "evaluation_criteria", "user_scenario"):
        assert field not in text
    assert TASK_0_USER in text
