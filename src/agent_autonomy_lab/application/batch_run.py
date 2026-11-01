"""Run every trial of a task list, score each simulation, and stop at the spend cap.

Each attempt keeps a row, whatever became of it, so nothing leaves the record without a
sign. A run the application failed or left incomplete stays a scored row, and only an
outside cause makes it ``INVALID`` (ADR-018). Only an ``INVALID`` run is retried, because
retrying a poor result until it passes would inflate the pass rate; the ``INVALID`` row
stays, and the retry is logged apart from the results.

The cap is checked before each simulation, before its scoring, and after it, so a run can
pass it by at most one of those stages and a stop always says so. The batch also stops when
it can no longer trust what it is measuring: when an agent turn sent something upstream's
agent would not, or a provider call cannot be priced, and when the runner or the scorer
itself raises.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_autonomy_lab.application.conversation_trace import MIN_MESSAGES
from agent_autonomy_lab.application.trace_live_run import LiveTrace, live_trace, record_live_run
from agent_autonomy_lab.domain.batch import BatchPlan, ModelPrice, SimulationAttempt, SpendLedger
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    EvaluatorOutput,
    ResultStatus,
)
from agent_autonomy_lab.domain.evaluation.run_standing import RunClass, RunStanding, run_standing
from agent_autonomy_lab.domain.events import Feedback, TraceEvent, stable_id
from agent_autonomy_lab.ports.batch import SimulationRunner, SimulationScorer
from agent_autonomy_lab.ports.trace_sink import TraceSink


@dataclass(frozen=True)
class SimulationRow:
    """One attempt and all that became of it.

    ``not_scored`` says why it has no results. ``trace_error`` says why it reached no trace.
    """

    attempt: SimulationAttempt
    standing: RunStanding | None
    events: tuple[TraceEvent, ...] = ()
    output: EvaluatorOutput | None = None
    judge_callback_calls: tuple[Any, ...] = ()
    judge_provider_calls: tuple[Any, ...] = ()
    trace: LiveTrace | None = None
    trace_error: str | None = None
    not_scored: str | None = None
    spent_usd_after: float = 0.0

    @property
    def results(self) -> tuple[EvaluationResult, ...]:
        return self.output.results if self.output else ()

    @property
    def is_invalid(self) -> bool:
        return self.standing is not None and self.standing.run_class is RunClass.INVALID


@dataclass
class BatchOutcome:
    plan: BatchPlan
    ledger: SpendLedger
    rows: list[SimulationRow] = field(default_factory=list)
    retries: list[dict[str, Any]] = field(default_factory=list)
    not_run: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


def _standing(attempt: SimulationAttempt) -> RunStanding | None:
    """How the run ended, or ``None`` for a reason the table does not name."""
    try:
        if attempt.unfinished is not None:
            return run_standing(
                None, raised_exception=True, stop_cause=attempt.unfinished.error_class
            )
        return run_standing(attempt.simulation.termination_reason)
    except ValueError:
        return None


def _trace_events(attempt: SimulationAttempt, user_model_id: str) -> tuple[TraceEvent, ...]:
    """The spans the results cite, which are the ones sent, so a citation can be followed."""
    recording = attempt.recording
    if recording is None or len(recording.transcript) < MIN_MESSAGES:
        return ()
    try:
        events, _ = live_trace(
            recording,
            attempt.simulation or attempt.unfinished,
            user_model_id,
            attempt.policy_version,
        )
    except ValueError:
        return ()  # The evaluators report what they cannot cite.
    return tuple(events)


def _subject(attempt: SimulationAttempt, events: tuple[TraceEvent, ...]) -> EvaluationSubject:
    raised = attempt.unfinished is not None
    return EvaluationSubject(
        simulation_id=attempt.simulation_id,
        simulation=attempt.simulation,
        events=events,
        scenario_id=attempt.task_id,
        raised_exception=raised,
        stop_cause=attempt.unfinished.error_class if raised else None,
    )


def result_feedback(
    results: Iterable[EvaluationResult], events: Sequence[TraceEvent]
) -> list[Feedback]:
    """Each result that says something, as feedback on its tool span or else on the root.

    ``NOT_APPLICABLE`` results are left out: there are many, and a trace reader gains nothing.
    """
    spans = {str(event.run_id): event for event in events if event.is_span}
    root = next((event for event in events if event.parent_run_id is None and event.is_span), None)
    feedback = []
    for result in results:
        span = spans.get(result.target or "", root)
        if span is None or result.status is ResultStatus.NOT_APPLICABLE:
            continue
        feedback.append(
            Feedback(
                feedback_id=stable_id(
                    "feedback",
                    result.simulation_id,
                    "live",
                    "evaluation",
                    result.evaluation_key,
                    result.target or "simulation",
                ),
                run_id=span.run_id,
                context=span.context,
                key=result.evaluation_key,
                score=result.score,
                comment=f"{result.status.value}: {result.reason}",
                value={
                    "status": result.status.value,
                    "evaluator_version": result.evaluator_version,
                    "failure_kind": result.failure_kind.value if result.failure_kind else None,
                    "invalid_reason": result.invalid_reason,
                    "target": result.target,
                },
            )
        )
    return feedback


class _Batch:
    def __init__(
        self,
        plan: BatchPlan,
        runner: SimulationRunner,
        scorer: SimulationScorer,
        new_sink: Callable[[], TraceSink],
        user_model_id: str,
        prices: dict[str, ModelPrice],
        on_row: Callable[[SimulationRow], None],
    ) -> None:
        self.plan = plan
        self.runner = runner
        self.scorer = scorer
        self.new_sink = new_sink
        self.user_model_id = user_model_id
        self.on_row = on_row
        self.outcome = BatchOutcome(plan, SpendLedger(prices))

    def stop(self, reason: str) -> None:
        self.outcome.stop_reason = self.outcome.stop_reason or reason

    def check_spend(self) -> None:
        ledger = self.outcome.ledger
        if ledger.total_usd >= self.plan.cap_usd:
            self.stop(
                f"Spend reached the ${self.plan.cap_usd:g} cap at list price "
                f"(${ledger.total_usd:.4f})."
            )
        elif ledger.unpriced_calls:
            self.stop(
                f"{ledger.unpriced_calls} provider call(s) could not be priced, "
                "so the cap cannot be held."
            )

    def run_all(self) -> BatchOutcome:
        for task_id, trial in self.plan.units():
            self.run_unit(task_id, trial)
        return self.outcome

    def run_unit(self, task_id: str, trial: int) -> None:
        for attempt_number in range(self.plan.max_retries + 1):
            self.check_spend()
            if self.outcome.stop_reason:
                if attempt_number == 0:
                    self.outcome.not_run.append(
                        {"task_id": task_id, "trial": trial, "reason": self.outcome.stop_reason}
                    )
                return
            try:
                attempt = self.runner.run(task_id, trial, attempt_number)
            except Exception as error:
                reason = f"The runner raised {type(error).__name__}: {error}"
                self.stop(reason)
                self.outcome.not_run.append({"task_id": task_id, "trial": trial, "reason": reason})
                return
            row = self.finish(attempt)
            self.outcome.rows.append(row)
            self.on_row(row)
            if not row.is_invalid or self.outcome.stop_reason:
                return
            if attempt_number < self.plan.max_retries:
                self.outcome.retries.append(
                    {
                        "task_id": task_id,
                        "trial": trial,
                        "attempt": attempt_number,
                        "simulation_id": attempt.simulation_id,
                        "because": row.standing.reason,
                        "cause": row.standing.cause,
                    }
                )

    def finish(self, attempt: SimulationAttempt) -> SimulationRow:
        ledger = self.outcome.ledger
        ledger.add(attempt.provider_calls)
        standing = _standing(attempt)
        events = _trace_events(attempt, self.user_model_id)
        if differences := attempt.fingerprint_differences:
            self.stop(
                f"Task {attempt.task_id} sent other values than upstream's llm_agent on "
                f"turns {', '.join(sorted(differences, key=int))}."
            )
            return self._unscored(attempt, standing, events, self.outcome.stop_reason)
        self.check_spend()
        if self.outcome.stop_reason:
            return self._unscored(attempt, standing, events, self.outcome.stop_reason)
        try:
            scored = self.scorer.score(_subject(attempt, events))
        except Exception as error:
            self.stop(f"The scorer raised {type(error).__name__}: {error}")
            return self._unscored(attempt, standing, events, self.outcome.stop_reason)
        ledger.add(scored.provider_calls)
        self.check_spend()  # The last simulation has no later check, and its results stay.
        trace, trace_error = self._send(attempt, scored.output, events)
        return SimulationRow(
            attempt=attempt,
            standing=standing,
            events=events,
            output=scored.output,
            judge_callback_calls=scored.callback_calls,
            judge_provider_calls=scored.provider_calls,
            trace=trace,
            trace_error=trace_error,
            spent_usd_after=ledger.total_usd,
        )

    def _unscored(
        self,
        attempt: SimulationAttempt,
        standing: RunStanding | None,
        events: tuple[TraceEvent, ...],
        reason: str,
    ) -> SimulationRow:
        trace, trace_error = self._send(attempt, None, events)
        return SimulationRow(
            attempt=attempt,
            standing=standing,
            events=events,
            trace=trace,
            trace_error=trace_error,
            not_scored=reason,
            spent_usd_after=self.outcome.ledger.total_usd,
        )

    def _send(
        self,
        attempt: SimulationAttempt,
        output: EvaluatorOutput | None,
        events: tuple[TraceEvent, ...],
    ) -> tuple[LiveTrace | None, str | None]:
        """Send the trace and its scores; a trace backend's trouble does not stop the batch."""
        if not events:
            return None, "The run left no conversation to trace."
        feedback = result_feedback(output.results, events) if output else []
        try:
            trace = record_live_run(
                attempt.recording,
                attempt.simulation or attempt.unfinished,
                self.new_sink(),
                self.user_model_id,
                attempt.policy_version,
                extra_feedback=feedback,
            )
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"
        return trace, None


def run_batch(
    plan: BatchPlan,
    runner: SimulationRunner,
    scorer: SimulationScorer,
    new_sink: Callable[[], TraceSink],
    user_model_id: str,
    prices: dict[str, ModelPrice],
    on_row: Callable[[SimulationRow], None] = lambda row: None,
) -> BatchOutcome:
    """Run ``plan`` and return every attempt's row; ``on_row`` sees each as it finishes.

    Each simulation gets its own sink from ``new_sink``. A sink keeps what it could not
    deliver, so sharing one would resend a failed trace with every later simulation, let one
    rejected trace fail them all, and deliver rows the report calls not sent.
    """
    return _Batch(plan, runner, scorer, new_sink, user_model_id, prices, on_row).run_all()
