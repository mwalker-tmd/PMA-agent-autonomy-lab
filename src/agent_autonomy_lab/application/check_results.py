"""What the deterministic checks share: building results, and naming a call's unit.

Each result is for one unit. A violation needs only the attempt, while a ``PASS``
needs the record to establish what happened, and every ``PASS`` or ``FAIL`` needs
a span to cite. Anything less is ``INSUFFICIENT_EVIDENCE``.
"""

from collections.abc import Iterable
from uuid import UUID

from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    EvaluatorOutput,
    FailureKind,
    ResultStatus,
)
from agent_autonomy_lab.domain.evaluation.trace_evidence import TraceEvidence
from agent_autonomy_lab.domain.simulations import SavedSimulation
from agent_autonomy_lab.domain.tool_timeline import ToolCallRecord, results_without_a_call

_LOST_CALL = "A tool result has no matching call, and the lost call could be any tool."
_NO_RECORD = "The run left no messages, so there is nothing to check."


class CheckResults:
    """Builds the results of one evaluator on one subject, with their shared fields."""

    def __init__(self, key: str, version: str, subject: EvaluationSubject) -> None:
        self._fields = {
            "evaluation_key": key,
            "evaluator_version": version,
            "simulation_id": subject.simulation_id,
            "scenario_id": subject.scenario_id,
        }
        self.results: list[EvaluationResult] = []

    def _add(self, target: str | None, status: ResultStatus, reason: str, **fields) -> None:
        self.results.append(
            EvaluationResult(status=status, reason=reason, target=target, **self._fields, **fields)
        )

    def passed(self, target: str | None, reason: str, evidence: Iterable[UUID]) -> None:
        self._decided(target, ResultStatus.PASS, reason, evidence)

    def failed(
        self,
        target: str | None,
        reason: str,
        evidence: Iterable[UUID],
        kind: FailureKind = FailureKind.VIOLATION,
        score: float | None = 0.0,
    ) -> None:
        self._decided(target, ResultStatus.FAIL, reason, evidence, kind, score)

    def _decided(
        self,
        target: str | None,
        status: ResultStatus,
        reason: str,
        evidence: Iterable[UUID],
        kind: FailureKind = FailureKind.VIOLATION,
        fail_score: float | None = 0.0,
    ) -> None:
        evidence_ids = tuple(evidence)
        if not evidence_ids:
            self.unknown(target, f"The trace has no span to cite for: {reason}")
        elif status is ResultStatus.FAIL:
            self._add(
                target,
                status,
                reason,
                score=fail_score,
                evidence_ids=evidence_ids,
                failure_kind=kind,
            )
        else:
            self._add(target, status, reason, score=1.0, evidence_ids=evidence_ids)

    def unknown(self, target: str | None, reason: str, evidence: Iterable[UUID] = ()) -> None:
        self._add(target, ResultStatus.INSUFFICIENT_EVIDENCE, reason, evidence_ids=tuple(evidence))

    def output(self, nothing_to_check: str | None = None) -> EvaluatorOutput:
        """The results so far; with none, the unit is the simulation and it does not apply."""
        if not self.results:
            self._add(None, ResultStatus.NOT_APPLICABLE, nothing_to_check)
        return EvaluatorOutput(results=tuple(self.results))


def unit_target(record: ToolCallRecord, evidence: TraceEvidence) -> str:
    """The call's tool span ``run_id``; with no span, its place in the record."""
    span = evidence.tool_span(record)
    if span is not None:
        return str(span.run_id)
    return f"message:{record.message_index}:{record.position}"


def no_result_reason(record: ToolCallRecord) -> str:
    return record.problem or f"{record.call.name} has no result."


def record_or_note(results: CheckResults, subject: EvaluationSubject) -> SavedSimulation | None:
    """Return the simulation, or note that it has no messages and return ``None``."""
    if subject.simulation is None or not subject.simulation.messages:
        results.unknown(None, _NO_RECORD)
        return None
    return subject.simulation


def note_lost_calls(results: CheckResults, simulation: SavedSimulation) -> None:
    for index, _ in results_without_a_call(simulation.messages):
        results.unknown(f"message:{index}", _LOST_CALL)
