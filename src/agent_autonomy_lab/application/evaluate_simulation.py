"""Run evaluators on one simulation so that every one of them leaves a result.

An evaluator that raises, or returns nothing usable, becomes an
``EVALUATOR_ERROR`` row instead of a gap in the denominators. An unusable result
costs only its own unit, so a proved violation beside it still counts. The run's
standing is then applied to every result (docs/EVALUATION.md, "Invalid runs").
"""

from collections.abc import Iterable

from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    EvaluatorOutput,
    RawEvaluatorOutput,
    ResultStatus,
    run_standing,
    settle_results,
)
from agent_autonomy_lab.domain.events import new_id
from agent_autonomy_lab.ports.evaluator import Evaluator


def _error_output(
    evaluator: Evaluator,
    subject: EvaluationSubject,
    reason: str,
    payload: dict[str, object],
    target: str | None = None,
) -> EvaluatorOutput:
    raw = RawEvaluatorOutput(
        output_id=new_id(),
        evaluation_key=evaluator.evaluation_key,
        evaluator_version=evaluator.version,
        simulation_id=subject.simulation_id,
        payload=payload,
    )
    result = EvaluationResult(
        evaluation_key=evaluator.evaluation_key,
        status=ResultStatus.EVALUATOR_ERROR,
        reason=reason,
        evaluator_version=evaluator.version,
        simulation_id=subject.simulation_id,
        scenario_id=subject.scenario_id,
        target=target,
        raw_output_id=raw.output_id,
    )
    return EvaluatorOutput(results=(result,), raw_outputs=(raw,))


def _result_errors(
    evaluator: Evaluator, subject: EvaluationSubject, result: EvaluationResult
) -> list[str]:
    """Describe why one result cannot be used; an empty list means it can."""
    event_ids = {event.event_id for event in subject.events}
    errors = []
    if result.evaluation_key != evaluator.evaluation_key:
        errors.append(f"returned evaluation_key {result.evaluation_key!r}")
    if result.evaluator_version != evaluator.version:
        errors.append(f"returned evaluator_version {result.evaluator_version!r}")
    if result.simulation_id != subject.simulation_id:
        errors.append(f"returned simulation_id {result.simulation_id!r}")
    if result.scenario_id != subject.scenario_id:
        errors.append(f"returned scenario_id {result.scenario_id!r}")
    for event_id in result.evidence_ids:
        if event_id not in event_ids:
            errors.append(f"cited event {event_id}, which is not in this simulation's trace")
    return errors


def _checked_output(
    evaluator: Evaluator, subject: EvaluationSubject, output: EvaluatorOutput
) -> EvaluatorOutput:
    """Keep each usable result and replace each unusable unit with an error row."""
    by_target: dict[str | None, list[EvaluationResult]] = {}
    for result in output.results:
        by_target.setdefault(result.target, []).append(result)
    results: list[EvaluationResult] = []
    raw_outputs = list(output.raw_outputs)
    for target, group in by_target.items():
        errors = [error for result in group for error in _result_errors(evaluator, subject, result)]
        if len(group) > 1:
            errors.insert(0, "returned more than one result for a unit")
        if not errors:
            results.extend(group)
            continue
        rejected = _error_output(
            evaluator,
            subject,
            f"The evaluator's output was rejected: {errors[0]}.",
            {"errors": errors, "rejected_results": [r.to_dict() for r in group]},
            target,
        )
        results.extend(rejected.results)
        raw_outputs.extend(rejected.raw_outputs)
    return EvaluatorOutput(results=tuple(results), raw_outputs=tuple(raw_outputs))


def _run_evaluator(evaluator: Evaluator, subject: EvaluationSubject) -> EvaluatorOutput:
    try:
        output = evaluator.evaluate(subject)
    except Exception as error:
        return _error_output(
            evaluator,
            subject,
            f"The evaluator raised {type(error).__name__}.",
            {"error_class": type(error).__name__, "error_message": str(error)},
        )
    if not isinstance(output, EvaluatorOutput):
        problem = f"returned a {type(output).__name__}, not an EvaluatorOutput"
    elif not output.results:
        problem = "returned no results"
    else:
        return _checked_output(evaluator, subject, output)
    return _error_output(
        evaluator,
        subject,
        f"The evaluator's output was rejected: {problem}.",
        {"errors": [problem]},
    )


def evaluate_simulation(
    subject: EvaluationSubject, evaluators: Iterable[Evaluator]
) -> EvaluatorOutput:
    """Score one simulation with every evaluator and settle the results.

    A run with no known standing cannot be scored, so no evaluator is called and
    each one gets an ``EVALUATOR_ERROR`` row, as for input that cannot be parsed.
    """
    evaluators = tuple(evaluators)
    try:
        standing = run_standing(
            subject.termination_reason,
            raised_exception=subject.raised_exception,
            stop_cause=subject.stop_cause,
        )
    except ValueError as error:
        outputs = [
            _error_output(
                evaluator, subject, f"The run cannot be scored: {error}.", {"error": str(error)}
            )
            for evaluator in evaluators
        ]
        standing = None
    else:
        outputs = [_run_evaluator(evaluator, subject) for evaluator in evaluators]
    results = [result for output in outputs for result in output.results]
    raw_outputs = tuple(raw for output in outputs for raw in output.raw_outputs)
    settled = settle_results(results, standing) if standing else tuple(results)
    return EvaluatorOutput(results=settled, raw_outputs=raw_outputs)
