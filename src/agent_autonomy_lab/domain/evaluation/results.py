"""The normalized evaluator result (docs/EVALUATION.md, "Required result format").

An ``EvaluationResult`` is what reports and denominators read. What an
evaluator returned before normalization, such as a judge's reply, is a separate
``RawEvaluatorOutput``, so an audit can compare the two and a report cannot
pick up unnormalized content by accident.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from agent_autonomy_lab.domain.events import TraceEvent, json_data
from agent_autonomy_lab.domain.simulations import SavedSimulation


class ResultStatus(StrEnum):
    """Only ``PASS`` and ``FAIL`` enter a pass-rate denominator."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EVALUATOR_ERROR = "EVALUATOR_ERROR"
    INVALID = "INVALID"


SCORED_STATUSES = frozenset({ResultStatus.PASS, ResultStatus.FAIL})


class FailureKind(StrEnum):
    """What a ``FAIL`` rests on, which decides whether it survives an invalid run.

    ``VIOLATION`` means the record proves a rule was broken, so an outage cannot
    hide it. ``OUTCOME`` means the run did not reach a good result, such as a
    τ-bench reward of ``0.0`` or a low rubric score, which says nothing about
    the application when an outside cause stopped the run. There is no default,
    because either wrong default would misreport a run without any error.
    """

    VIOLATION = "violation"
    OUTCOME = "outcome"


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_optional_text(name: str, value: object) -> None:
    if value is not None:
        _require_text(name, value)


@dataclass(frozen=True)
class RawEvaluatorOutput:
    """An evaluator's output before normalization, kept for audit and review.

    ``payload`` may hold anything the evaluator saw or produced, so it stays out
    of ``EvaluationResult`` and its ``to_dict()``. It is copied as strict JSON.
    """

    output_id: UUID
    evaluation_key: str
    evaluator_version: str
    simulation_id: str
    payload: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.output_id, UUID):
            raise ValueError("output_id must be a UUID")
        for name in ("evaluation_key", "evaluator_version", "simulation_id"):
            _require_text(name, getattr(self, name))
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")
        object.__setattr__(self, "payload", json_data(self.payload))


@dataclass(frozen=True)
class EvaluationResult:
    """One normalized result for one unit of one metric.

    ``target`` names the unit inside a simulation when a metric has several, such
    as a write's tool span ``run_id``; it is ``None`` when the unit is the
    simulation. ``evidence_ids`` are ``trace-event/1`` ``event_id`` values.

    The constructor rejects a result that could be misread as more than it
    proves: a ``PASS`` or ``FAIL`` needs evidence, a score belongs only on a
    ``PASS`` or ``FAIL``, a ``FAIL`` states its ``failure_kind``, and an
    ``INVALID`` result states its ``invalid_reason``.
    """

    evaluation_key: str
    status: ResultStatus
    reason: str
    evaluator_version: str
    simulation_id: str
    score: float | None = None
    evidence_ids: tuple[UUID, ...] = ()
    scenario_id: str | None = None
    target: str | None = None
    failure_kind: FailureKind | None = None
    invalid_reason: str | None = None
    raw_output_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("evaluation_key", "reason", "evaluator_version", "simulation_id"):
            _require_text(name, getattr(self, name))
        for name in ("scenario_id", "target", "invalid_reason"):
            _require_optional_text(name, getattr(self, name))
        object.__setattr__(self, "status", ResultStatus(self.status))
        if self.failure_kind is not None:
            object.__setattr__(self, "failure_kind", FailureKind(self.failure_kind))
        evidence = tuple(self.evidence_ids)
        if not all(isinstance(event_id, UUID) for event_id in evidence):
            raise ValueError("evidence_ids must be trace-event/1 event_id UUIDs")
        object.__setattr__(self, "evidence_ids", evidence)
        if self.raw_output_id is not None and not isinstance(self.raw_output_id, UUID):
            raise ValueError("raw_output_id must be a UUID")
        self._check_score()
        self._check_status_fields()

    def _check_score(self) -> None:
        score = self.score
        if score is None:
            return
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("score must be a number")
        if not math.isfinite(score):
            raise ValueError("score must be finite")
        if self.status not in SCORED_STATUSES:
            raise ValueError(f"a {self.status} result has no score")

    def _check_status_fields(self) -> None:
        if self.status in SCORED_STATUSES and not self.evidence_ids:
            raise ValueError(f"a {self.status} result needs evidence_ids")
        if (self.status is ResultStatus.FAIL) != (self.failure_kind is not None):
            raise ValueError("failure_kind is required on a FAIL and belongs only there")
        if (self.status is ResultStatus.INVALID) != (self.invalid_reason is not None):
            raise ValueError("invalid_reason is required on INVALID and belongs only there")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready result; the raw output is linked, never embedded."""
        return {
            "evaluation_key": self.evaluation_key,
            "status": self.status.value,
            "score": self.score,
            "evidence_ids": [str(event_id) for event_id in self.evidence_ids],
            "reason": self.reason,
            "evaluator_version": self.evaluator_version,
            "scenario_id": self.scenario_id,
            "simulation_id": self.simulation_id,
            "target": self.target,
            "failure_kind": self.failure_kind.value if self.failure_kind else None,
            "invalid_reason": self.invalid_reason,
            "raw_output_id": str(self.raw_output_id) if self.raw_output_id else None,
        }


@dataclass(frozen=True)
class EvaluatorOutput:
    """Normalized results and the raw outputs behind them, held apart.

    A result's ``raw_output_id`` must name a raw output here from the same
    evaluator and simulation, so an audit link cannot reach another run's reply.
    """

    results: tuple[EvaluationResult, ...]
    raw_outputs: tuple[RawEvaluatorOutput, ...] = ()

    def __post_init__(self) -> None:
        results = tuple(self.results)
        raw_outputs = tuple(self.raw_outputs)
        if not all(isinstance(result, EvaluationResult) for result in results):
            raise ValueError("results must be EvaluationResult records")
        if not all(isinstance(raw, RawEvaluatorOutput) for raw in raw_outputs):
            raise ValueError("raw_outputs must be RawEvaluatorOutput records")
        object.__setattr__(self, "results", results)
        object.__setattr__(self, "raw_outputs", raw_outputs)
        known = {raw.output_id: raw for raw in raw_outputs}
        for result in results:
            if result.raw_output_id is None:
                continue
            raw = known.get(result.raw_output_id)
            if raw is None:
                raise ValueError(f"raw_output_id {result.raw_output_id} is not in raw_outputs")
            if (raw.evaluation_key, raw.evaluator_version, raw.simulation_id) != (
                result.evaluation_key,
                result.evaluator_version,
                result.simulation_id,
            ):
                raise ValueError(f"raw output {raw.output_id} belongs to another result")


@dataclass(frozen=True)
class EvaluationSubject:
    """What an evaluator scores: one simulation's record and how the run ended.

    ``simulation`` is ``None`` for a run that raised (``raised_exception``),
    which leaves no ``SimulationRun``, and ``events`` may then hold a partial
    trace. ``stop_cause`` is what the record shows stopped the run: a provider,
    network, or simulator cause, or the ``error_class`` of a raised exception.
    An injected fault (``fault_injected: true``) tests recovery, so it is never
    a ``stop_cause``.
    """

    simulation_id: str
    simulation: SavedSimulation | None = None
    events: tuple[TraceEvent, ...] = ()
    scenario_id: str | None = None
    raised_exception: bool = False
    stop_cause: str | None = None

    def __post_init__(self) -> None:
        _require_text("simulation_id", self.simulation_id)
        for name in ("scenario_id", "stop_cause"):
            _require_optional_text(name, getattr(self, name))
        object.__setattr__(self, "events", tuple(self.events))
        record = self.simulation
        if record is not None and record.simulation_id != self.simulation_id:
            raise ValueError("simulation does not match simulation_id")
        if any(event.context.simulation_id != self.simulation_id for event in self.events):
            raise ValueError("every event must belong to simulation_id")

    @property
    def termination_reason(self) -> str | None:
        return self.simulation.termination_reason if self.simulation else None
