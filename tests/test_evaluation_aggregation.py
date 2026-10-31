from uuid import uuid4

import pytest

from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    FailureKind,
    ResultStatus,
    aggregate,
    roll_up,
)


def make_result(status, simulation_id, key="metric", target=None, invalid_reason=None):
    scored = status in (ResultStatus.PASS, ResultStatus.FAIL)
    return EvaluationResult(
        evaluation_key=key,
        status=status,
        reason="A reason.",
        evaluator_version="v1",
        simulation_id=simulation_id,
        target=target,
        evidence_ids=(uuid4(),) if scored else (),
        failure_kind=FailureKind.VIOLATION if status is ResultStatus.FAIL else None,
        invalid_reason=invalid_reason,
    )


def test_only_pass_and_fail_enter_the_denominator_and_every_status_is_counted():
    statuses = [
        (ResultStatus.PASS, None),
        (ResultStatus.PASS, None),
        (ResultStatus.PASS, None),
        (ResultStatus.FAIL, None),
        (ResultStatus.NOT_APPLICABLE, None),
        (ResultStatus.INSUFFICIENT_EVIDENCE, None),
        (ResultStatus.INSUFFICIENT_EVIDENCE, None),
        (ResultStatus.EVALUATOR_ERROR, None),
        (ResultStatus.INVALID, "user_error"),
        (ResultStatus.INVALID, "infrastructure_error"),
        (ResultStatus.INVALID, "infrastructure_error"),
    ]
    results = [
        make_result(status, f"sim-{i}", invalid_reason=reason)
        for i, (status, reason) in enumerate(statuses)
    ]
    metric = aggregate(results)["metric"]
    assert (metric.numerator, metric.denominator) == (3, 4)
    assert metric.pass_rate == 0.75
    assert metric.not_applicable == 1
    assert metric.insufficient_evidence == 2
    assert metric.evaluator_error == 1
    assert metric.invalid == 3
    assert metric.invalid_reasons == {"infrastructure_error": 2, "user_error": 1}
    assert metric.total == len(results)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ResultStatus.EVALUATOR_ERROR, None),
        (ResultStatus.INSUFFICIENT_EVIDENCE, None),
        (ResultStatus.INVALID, "infrastructure_error"),
    ],
)
def test_unknowns_never_count_as_passing(status, reason):
    alone = aggregate([make_result(status, "sim-1", invalid_reason=reason)])["metric"]
    assert (alone.numerator, alone.denominator, alone.pass_rate) == (0, 0, None)

    with_a_failure = aggregate(
        [
            make_result(status, "sim-1", invalid_reason=reason),
            make_result(ResultStatus.FAIL, "sim-2"),
        ]
    )["metric"]
    assert (with_a_failure.numerator, with_a_failure.denominator) == (0, 1)
    assert with_a_failure.pass_rate == 0


def test_each_metric_is_aggregated_on_its_own_and_sorted():
    metrics = aggregate(
        [
            make_result(ResultStatus.FAIL, "sim-1", key="zeta"),
            make_result(ResultStatus.PASS, "sim-1", key="alpha"),
        ]
    )
    assert list(metrics) == ["alpha", "zeta"]
    assert metrics["alpha"].pass_rate == 1
    assert metrics["zeta"].pass_rate == 0


def test_several_units_of_one_simulation_each_count():
    metric = aggregate(
        [
            make_result(ResultStatus.FAIL, "sim-1", target="write-1"),
            make_result(ResultStatus.PASS, "sim-1", target="write-2"),
        ]
    )["metric"]
    assert (metric.numerator, metric.denominator) == (1, 2)


def test_a_repeated_unit_is_refused_rather_than_inflating_the_denominator():
    repeated = [make_result(ResultStatus.PASS, "sim-1") for _ in range(2)]
    with pytest.raises(ValueError, match="more than one result"):
        aggregate(repeated)


def test_no_results_means_no_metrics():
    assert aggregate([]) == {}


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["PASS", "NOT_APPLICABLE"], "PASS"),
        (["NOT_APPLICABLE", "NOT_APPLICABLE"], "NOT_APPLICABLE"),
        (["PASS", "INSUFFICIENT_EVIDENCE"], "INSUFFICIENT_EVIDENCE"),
        (["INSUFFICIENT_EVIDENCE", "EVALUATOR_ERROR", "PASS"], "EVALUATOR_ERROR"),
        (["EVALUATOR_ERROR", "FAIL", "PASS"], "FAIL"),
        (["INVALID", "INVALID"], "INVALID"),
        (["INVALID", "FAIL"], "FAIL"),
    ],
)
def test_roll_up_follows_the_contracts_order(statuses, expected):
    assert roll_up(statuses) == expected


def test_roll_up_needs_a_status():
    with pytest.raises(ValueError, match="at least one"):
        roll_up([])
