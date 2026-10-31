from uuid import uuid4

import pytest
from tau2.data_model.simulation import TerminationReason

from agent_autonomy_lab.domain.evaluation import (
    RAISED_EXCEPTION,
    EvaluationResult,
    FailureKind,
    ResultStatus,
    RunClass,
    aggregate,
    run_standing,
    settle_results,
)

TABLE = {
    "agent_stop": None,
    "user_stop": None,
    "agent_error": ResultStatus.FAIL,
    "max_steps": ResultStatus.FAIL,
    "too_many_errors": ResultStatus.FAIL,
    "context_window_exceeded": ResultStatus.FAIL,
    "infrastructure_error": ResultStatus.INVALID,
    "user_error": ResultStatus.INVALID,
    "timeout": ResultStatus.FAIL,
    "unexpected_error": ResultStatus.FAIL,
}


def make_result(status=ResultStatus.PASS, kind=None, key="metric", simulation_id="sim-1"):
    return EvaluationResult(
        evaluation_key=key,
        status=status,
        reason="Evaluator's reason.",
        evaluator_version="v1",
        simulation_id=simulation_id,
        score=2 if status is ResultStatus.PASS else None,
        evidence_ids=(uuid4(),) if status in (ResultStatus.PASS, ResultStatus.FAIL) else (),
        failure_kind=kind,
    )


@pytest.mark.parametrize(("reason", "expected"), TABLE.items())
def test_each_termination_reason_maps_as_in_the_contract(reason, expected):
    assert run_standing(reason).result_status is expected


def test_the_table_covers_every_tau2_termination_reason():
    assert {reason.value for reason in TerminationReason} == set(TABLE)


@pytest.mark.parametrize("reason", ["timeout", "unexpected_error"])
def test_a_cause_dependent_reason_is_invalid_only_with_evidence_of_an_outside_cause(reason):
    assert run_standing(reason).run_class is RunClass.APPLICATION_FAILURE
    assert run_standing(reason, stop_cause="").run_class is RunClass.APPLICATION_FAILURE
    standing = run_standing(reason, stop_cause="provider returned 503")
    assert standing.run_class is RunClass.INVALID
    assert standing.cause == "provider returned 503"


def test_an_application_failure_stays_one_whatever_cause_is_offered():
    assert run_standing("max_steps", stop_cause="503").run_class is RunClass.APPLICATION_FAILURE


def test_a_run_that_raised_is_invalid_and_reports_its_error_class():
    standing = run_standing(None, raised_exception=True, stop_cause="APIConnectionError")
    assert standing.run_class is RunClass.INVALID
    assert (standing.reason, standing.cause) == (RAISED_EXCEPTION, "APIConnectionError")


@pytest.mark.parametrize("reason", [None, "", "new_reason", ["agent_stop"]])
def test_a_reason_the_table_does_not_name_gets_no_guessed_standing(reason):
    with pytest.raises(ValueError, match="no standing"):
        run_standing(reason)


def test_a_completed_run_is_scored_normally():
    results = [
        make_result(),
        make_result(ResultStatus.FAIL, FailureKind.OUTCOME, key="other"),
    ]
    assert settle_results(results, run_standing("agent_stop")) == tuple(results)


def test_an_invalid_run_leaves_every_denominator_and_is_counted_with_its_reason():
    results = [
        make_result(key="a"),
        make_result(ResultStatus.FAIL, FailureKind.OUTCOME, key="b"),
        make_result(ResultStatus.NOT_APPLICABLE, key="c"),
        make_result(ResultStatus.INSUFFICIENT_EVIDENCE, key="d"),
        make_result(ResultStatus.EVALUATOR_ERROR, key="e"),
    ]
    settled = settle_results(results, run_standing("infrastructure_error"))
    assert {result.status for result in settled} == {ResultStatus.INVALID}
    assert all(result.score is None and result.failure_kind is None for result in settled)
    for metric in aggregate(settled).values():
        assert (metric.numerator, metric.denominator, metric.pass_rate) == (0, 0, None)
        assert metric.invalid == 1
        assert metric.invalid_reasons == {"infrastructure_error": 1}


def test_an_invalid_result_names_the_cause_and_what_the_evaluator_said():
    original = make_result(ResultStatus.FAIL, FailureKind.OUTCOME)
    [settled] = settle_results([original], run_standing("timeout", stop_cause="provider 503"))
    assert settled.invalid_reason == "timeout"
    assert "provider 503" in settled.reason
    assert "FAIL" in settled.reason
    assert "Evaluator's reason." in settled.reason
    assert settled.evidence_ids == original.evidence_ids


def test_a_violation_proved_in_an_invalid_run_stays_a_fail():
    violation = make_result(ResultStatus.FAIL, FailureKind.VIOLATION)
    settled = settle_results([violation], run_standing("user_error"))
    assert settled == (violation,)
    metric = aggregate(settled)["metric"]
    assert (metric.numerator, metric.denominator, metric.invalid) == (0, 1, 0)


def test_a_zero_reward_on_an_invalid_run_is_not_a_proved_violation():
    outcome = make_result(ResultStatus.FAIL, FailureKind.OUTCOME)
    [settled] = settle_results([outcome], run_standing("infrastructure_error"))
    assert settled.status is ResultStatus.INVALID


@pytest.mark.parametrize("kind", list(FailureKind))
def test_an_application_failure_stays_a_fail_in_the_denominator(kind):
    failure = make_result(ResultStatus.FAIL, kind)
    settled = settle_results([failure], run_standing("max_steps"))
    assert settled == (failure,)
    metric = aggregate(settled)["metric"]
    assert (metric.numerator, metric.denominator) == (0, 1)


def test_a_pass_needs_the_whole_record():
    [settled] = settle_results([make_result()], run_standing("too_many_errors"))
    assert settled.status is ResultStatus.INSUFFICIENT_EVIDENCE
    assert settled.score is None
    assert "whole record" in settled.reason


@pytest.mark.parametrize(
    "status",
    [ResultStatus.NOT_APPLICABLE, ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.EVALUATOR_ERROR],
)
def test_an_application_failure_leaves_the_other_statuses_alone(status):
    result = make_result(status)
    assert settle_results([result], run_standing("agent_error")) == (result,)
