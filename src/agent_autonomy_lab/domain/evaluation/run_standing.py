"""Map how a run ended to its standing (docs/EVALUATION.md, "Invalid runs"; ADR-018).

The metrics evaluate the agentic application, not the infrastructure under it.
A failure the application caused stays a ``FAIL``. A run an outside cause
stopped is ``INVALID``: its results leave every denominator and are counted with
their reason, except a violation already proved in its record.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from agent_autonomy_lab.domain.evaluation.results import (
    EvaluationResult,
    FailureKind,
    ResultStatus,
)

COMPLETED_REASONS = frozenset({"agent_stop", "user_stop"})
APPLICATION_FAILURE_REASONS = frozenset(
    {"agent_error", "max_steps", "too_many_errors", "context_window_exceeded"}
)
INVALID_REASONS = frozenset({"infrastructure_error", "user_error"})
CAUSE_DEPENDENT_REASONS = frozenset({"timeout", "unexpected_error"})

RAISED_EXCEPTION = "raised_exception"
"""The reason reported for a run that raised, which has no ``termination_reason``."""


class RunClass(StrEnum):
    COMPLETED = "completed"
    APPLICATION_FAILURE = "application_failure"
    INVALID = "invalid"


@dataclass(frozen=True)
class RunStanding:
    run_class: RunClass
    reason: str
    cause: str | None = None

    @property
    def result_status(self) -> ResultStatus | None:
        """The contract table's result; ``None`` means the run is scored normally."""
        return {
            RunClass.COMPLETED: None,
            RunClass.APPLICATION_FAILURE: ResultStatus.FAIL,
            RunClass.INVALID: ResultStatus.INVALID,
        }[self.run_class]


def run_standing(
    termination_reason: str | None,
    *,
    raised_exception: bool = False,
    stop_cause: str | None = None,
) -> RunStanding:
    """Classify a run from the invalid-runs table.

    ``timeout`` and ``unexpected_error`` are application failures unless
    ``stop_cause`` gives positive evidence of an outside cause, so an unexplained
    failure cannot be excluded. A reason the table does not name raises
    ``ValueError`` rather than receiving a guessed standing.
    """
    if raised_exception:
        return RunStanding(RunClass.INVALID, RAISED_EXCEPTION, stop_cause)
    if not isinstance(termination_reason, str):
        raise ValueError(f"no standing for termination_reason {termination_reason!r}")
    if termination_reason in COMPLETED_REASONS:
        return RunStanding(RunClass.COMPLETED, termination_reason)
    if termination_reason in APPLICATION_FAILURE_REASONS:
        return RunStanding(RunClass.APPLICATION_FAILURE, termination_reason)
    if termination_reason in INVALID_REASONS:
        return RunStanding(RunClass.INVALID, termination_reason, stop_cause)
    if termination_reason in CAUSE_DEPENDENT_REASONS:
        if stop_cause:
            return RunStanding(RunClass.INVALID, termination_reason, stop_cause)
        return RunStanding(RunClass.APPLICATION_FAILURE, termination_reason)
    raise ValueError(f"no standing for termination_reason {termination_reason!r}")


def _settle_invalid(result: EvaluationResult, standing: RunStanding) -> EvaluationResult:
    if result.failure_kind is FailureKind.VIOLATION:
        return result
    cause = f" ({standing.cause})" if standing.cause else ""
    return replace(
        result,
        status=ResultStatus.INVALID,
        score=None,
        failure_kind=None,
        invalid_reason=standing.reason,
        reason=f"Run stopped by {standing.reason}{cause}, so this says nothing about the "
        f"application. Evaluator said {result.status}: {result.reason}",
    )


def _settle_application_failure(
    result: EvaluationResult, standing: RunStanding
) -> EvaluationResult:
    if result.status is not ResultStatus.PASS:
        return result
    return replace(
        result,
        status=ResultStatus.INSUFFICIENT_EVIDENCE,
        score=None,
        reason=f"A PASS needs the whole record, and the run ended early by {standing.reason}. "
        f"Evaluator said PASS: {result.reason}",
    )


def settle_results(
    results: Iterable[EvaluationResult], standing: RunStanding
) -> tuple[EvaluationResult, ...]:
    """Apply a run's standing to its results.

    On an invalid run everything becomes ``INVALID`` except a proved violation.
    On an application failure every ``FAIL`` stays, and a ``PASS`` becomes
    ``INSUFFICIENT_EVIDENCE``, because a ``PASS`` needs the whole record.
    """
    if standing.run_class is RunClass.INVALID:
        return tuple(_settle_invalid(result, standing) for result in results)
    if standing.run_class is RunClass.APPLICATION_FAILURE:
        return tuple(_settle_application_failure(result, standing) for result in results)
    return tuple(results)
