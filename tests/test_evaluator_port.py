from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    EvaluatorOutput,
    FailureKind,
    RawEvaluatorOutput,
    ResultStatus,
    aggregate,
    judge_output,
)
from agent_autonomy_lab.domain.events import EventType
from agent_autonomy_lab.domain.simulations import SavedSimulation
from agent_autonomy_lab.ports.evaluator import CannedEvaluator, Evaluator
from tests.conftest import make_context, make_event

SIM = "sim-1"
EVENT = make_event(EventType.CONVERSATION, make_context(SIM))


def make_subject(termination_reason="agent_stop", **overrides):
    simulation = SavedSimulation(
        simulation_id=SIM,
        task_id="0",
        start_time=datetime(2026, 10, 31, tzinfo=UTC),
        duration_seconds=1.0,
        termination_reason=termination_reason,
        messages=(),
    )
    fields = {"simulation_id": SIM, "simulation": simulation, "events": (EVENT,)}
    return EvaluationSubject(**(fields | overrides))


def make_output(status=ResultStatus.PASS, key="metric", **overrides):
    fields = {
        "evaluation_key": key,
        "status": status,
        "reason": "A reason.",
        "evaluator_version": "v1",
        "simulation_id": SIM,
    }
    if status in (ResultStatus.PASS, ResultStatus.FAIL):
        fields["evidence_ids"] = (EVENT.event_id,)
    if status is ResultStatus.FAIL:
        fields["failure_kind"] = FailureKind.VIOLATION
    return EvaluatorOutput(results=(EvaluationResult(**(fields | overrides)),))


def canned(output, key="metric"):
    return CannedEvaluator(key, "v1", {SIM: output})


def test_the_test_double_satisfies_the_port_and_records_its_subjects():
    evaluator: Evaluator = canned(make_output())
    subject = make_subject()
    assert evaluator.evaluate(subject).results[0].status is ResultStatus.PASS
    assert evaluator.subjects == [subject]


def test_results_and_raw_outputs_come_back_separately():
    stored = {"score": 2, "reasoning": "RAW-JUDGE-SENTINEL"}
    output = judge_output(
        stored,
        evaluation_key="metric",
        evaluator_version="v1",
        simulation_id=SIM,
        failure_kind=FailureKind.OUTCOME,
        evidence_ids=(EVENT.event_id,),
    )
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    assert evaluated.results == output.results
    assert evaluated.raw_outputs == output.raw_outputs
    assert "RAW-JUDGE-SENTINEL" not in str(evaluated.results[0].to_dict())


def test_an_evaluator_that_raises_leaves_a_row_that_never_passes():
    evaluator = CannedEvaluator("metric", "v1", error=RuntimeError("provider said no"))
    evaluated = evaluate_simulation(
        make_subject(), [evaluator, canned(make_output(key="other"), "other")]
    )
    by_key = {result.evaluation_key: result for result in evaluated.results}
    assert by_key["metric"].status is ResultStatus.EVALUATOR_ERROR
    assert by_key["metric"].reason == "The evaluator raised RuntimeError."
    assert by_key["other"].status is ResultStatus.PASS
    assert evaluated.raw_outputs[0].payload["error_message"] == "provider said no"
    metric = aggregate(evaluated.results)["metric"]
    assert (metric.denominator, metric.evaluator_error) == (0, 1)


def test_a_judge_failure_with_no_score_reaches_the_denominator_report_as_an_error():
    output = judge_output(
        {"score": None, "comment": "400 messages: at least one message is required"},
        evaluation_key="metric",
        evaluator_version="v1",
        simulation_id=SIM,
        failure_kind=FailureKind.OUTCOME,
        evidence_ids=(EVENT.event_id,),
    )
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    metric = aggregate(evaluated.results)["metric"]
    assert (metric.numerator, metric.denominator, metric.evaluator_error) == (0, 0, 1)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (EvaluatorOutput(results=()), "returned no results"),
        (None, "not an EvaluatorOutput"),
        (make_output(key="another"), "evaluation_key"),
        (make_output(evaluator_version="v2"), "evaluator_version"),
        (make_output(simulation_id="sim-2"), "simulation_id"),
        (make_output(scenario_id="scenario"), "scenario_id"),
        (make_output(evidence_ids=(uuid4(),)), "not in this simulation's trace"),
        (
            EvaluatorOutput(results=make_output().results + make_output().results),
            "more than one result",
        ),
    ],
)
def test_an_unusable_output_becomes_an_error_row(output, message):
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    [result] = evaluated.results
    assert result.status is ResultStatus.EVALUATOR_ERROR
    assert message in result.reason


def test_a_rejected_output_is_kept_for_audit():
    raw = RawEvaluatorOutput(uuid4(), "metric", "v1", SIM, {"judge": "RAW"})
    bad = EvaluatorOutput(
        results=(
            EvaluationResult(
                evaluation_key="metric",
                status=ResultStatus.PASS,
                reason="Cites nothing in this trace.",
                evaluator_version="v1",
                simulation_id=SIM,
                evidence_ids=(uuid4(),),
                raw_output_id=raw.output_id,
            ),
        ),
        raw_outputs=(raw,),
    )
    evaluated = evaluate_simulation(make_subject(), [canned(bad)])
    assert evaluated.raw_outputs[0] == raw
    assert evaluated.raw_outputs[1].payload["rejected_results"][0]["status"] == "PASS"


def test_raw_outputs_are_kept_when_an_evaluator_returns_no_results():
    raw = RawEvaluatorOutput(uuid4(), "metric", "v1", SIM, {"judge": "RAW"})
    output = EvaluatorOutput(results=(), raw_outputs=(raw,))
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    assert evaluated.results[0].status is ResultStatus.EVALUATOR_ERROR
    assert evaluated.raw_outputs[0] == raw
    assert len(evaluated.raw_outputs) == 2


def test_a_reused_raw_output_id_costs_only_the_evaluator_that_reused_it():
    output_id = uuid4()
    first = RawEvaluatorOutput(output_id, "first", "v1", SIM, {"judge": "one"})
    second = RawEvaluatorOutput(output_id, "second", "v1", SIM, {"judge": "two"})
    evaluators = [
        canned(
            EvaluatorOutput(
                results=(replace(make_output(key="first").results[0], raw_output_id=output_id),),
                raw_outputs=(first,),
            ),
            "first",
        ),
        canned(
            EvaluatorOutput(
                results=(replace(make_output(key="second").results[0], raw_output_id=output_id),),
                raw_outputs=(second,),
            ),
            "second",
        ),
        canned(make_output(key="third"), "third"),
    ]
    evaluated = evaluate_simulation(make_subject(), evaluators)
    by_key = {result.evaluation_key: result for result in evaluated.results}
    assert by_key["first"].status is ResultStatus.PASS
    assert by_key["first"].raw_output_id == output_id
    assert by_key["second"].status is ResultStatus.EVALUATOR_ERROR
    assert "reused raw output id" in by_key["second"].reason
    assert by_key["third"].status is ResultStatus.PASS
    assert first in evaluated.raw_outputs
    assert second not in evaluated.raw_outputs


def per_write(*results):
    return EvaluatorOutput(results=tuple(r for o in results for r in o.results))


def test_a_rejected_unit_does_not_cost_a_proved_violation_beside_it():
    output = per_write(
        make_output(ResultStatus.FAIL, target="write-1"),
        make_output(target="write-2", evidence_ids=(uuid4(),)),
    )
    evaluated = evaluate_simulation(make_subject("infrastructure_error"), [canned(output)])
    by_target = {result.target: result for result in evaluated.results}
    assert by_target["write-1"].status is ResultStatus.FAIL
    assert by_target["write-1"].failure_kind is FailureKind.VIOLATION
    assert by_target["write-2"].status is ResultStatus.INVALID
    metric = aggregate(evaluated.results)["metric"]
    assert (metric.denominator, metric.invalid) == (1, 1)


def test_a_rejected_unit_is_an_error_row_for_that_unit_only():
    output = per_write(
        make_output(target="write-1"),
        make_output(target="write-2", evidence_ids=(uuid4(),)),
    )
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    metric = aggregate(evaluated.results)["metric"]
    assert (metric.numerator, metric.denominator, metric.evaluator_error) == (1, 1, 1)
    [error] = [r for r in evaluated.results if r.status is ResultStatus.EVALUATOR_ERROR]
    assert error.target == "write-2"


def test_a_repeated_unit_leaves_one_error_row_and_aggregates():
    output = per_write(make_output(target="write-1"), make_output(target="write-1"))
    evaluated = evaluate_simulation(make_subject(), [canned(output)])
    assert aggregate(evaluated.results)["metric"].evaluator_error == 1
    assert len(evaluated.results) == 1


def test_a_malformed_output_built_inside_an_evaluator_does_not_stop_the_others():
    class Malformed:
        evaluation_key = "metric"
        version = "v1"

        def evaluate(self, subject):
            return EvaluatorOutput(results=(object(),))

    evaluated = evaluate_simulation(
        make_subject(), [Malformed(), canned(make_output(key="other"), "other")]
    )
    by_key = {result.evaluation_key: result.status for result in evaluated.results}
    assert by_key == {"metric": ResultStatus.EVALUATOR_ERROR, "other": ResultStatus.PASS}


@pytest.mark.parametrize("reason", [None, "new_reason"])
def test_a_run_with_no_known_standing_calls_no_evaluator_and_leaves_error_rows(reason):
    evaluator = canned(make_output())
    evaluated = evaluate_simulation(make_subject(reason), [evaluator])
    assert evaluator.subjects == []
    assert [result.status for result in evaluated.results] == [ResultStatus.EVALUATOR_ERROR]


def test_a_subject_with_no_simulation_and_no_exception_has_no_standing():
    evaluated = evaluate_simulation(EvaluationSubject(simulation_id=SIM), [canned(make_output())])
    assert evaluated.results[0].status is ResultStatus.EVALUATOR_ERROR


def test_a_run_that_raised_has_no_simulation_and_is_invalid():
    subject = EvaluationSubject(
        simulation_id=SIM,
        events=(EVENT,),
        raised_exception=True,
        stop_cause="APIConnectionError",
    )
    evaluated = evaluate_simulation(subject, [canned(make_output(ResultStatus.NOT_APPLICABLE))])
    [result] = evaluated.results
    assert result.status is ResultStatus.INVALID
    assert result.invalid_reason == "raised_exception"
    assert "APIConnectionError" in result.reason


def test_an_invalid_run_settles_results_end_to_end_and_keeps_a_proved_violation():
    evaluators = [
        canned(make_output(key="passed"), "passed"),
        canned(
            make_output(ResultStatus.FAIL, key="outcome", failure_kind=FailureKind.OUTCOME),
            "outcome",
        ),
        canned(make_output(ResultStatus.FAIL, key="violation"), "violation"),
        CannedEvaluator("errored", "v1", error=RuntimeError("no record")),
    ]
    evaluated = evaluate_simulation(make_subject("infrastructure_error"), evaluators)
    metrics = aggregate(evaluated.results)
    for key in ("passed", "outcome", "errored"):
        assert metrics[key].denominator == 0
        assert metrics[key].invalid_reasons == {"infrastructure_error": 1}
    assert (metrics["violation"].denominator, metrics["violation"].invalid) == (1, 0)


def test_an_application_failure_keeps_its_fail_end_to_end():
    output = make_output(ResultStatus.FAIL, failure_kind=FailureKind.OUTCOME)
    evaluated = evaluate_simulation(make_subject("max_steps"), [canned(output)])
    metric = aggregate(evaluated.results)["metric"]
    assert (metric.numerator, metric.denominator, metric.invalid) == (0, 1, 0)


def test_a_subject_must_be_about_one_simulation():
    other = make_event(EventType.CONVERSATION, make_context("sim-2"))
    with pytest.raises(ValueError, match="every event"):
        EvaluationSubject(simulation_id=SIM, events=(other,))
    with pytest.raises(ValueError, match="does not match"):
        make_subject(simulation_id="sim-2", events=())
