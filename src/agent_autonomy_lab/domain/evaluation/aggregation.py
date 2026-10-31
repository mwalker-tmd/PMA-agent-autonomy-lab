"""Numerators and denominators per metric (docs/EVALUATION.md, "Rules for every metric").

Every status is counted, so results that cannot enter a pass rate stay visible
beside it. A metric with no ``PASS`` or ``FAIL`` has no pass rate at all, rather
than a rate of 0 or 1.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from agent_autonomy_lab.domain.evaluation.results import EvaluationResult, ResultStatus

_ROLL_UP_ORDER = (
    ResultStatus.FAIL,
    ResultStatus.INVALID,
    ResultStatus.EVALUATOR_ERROR,
    ResultStatus.INSUFFICIENT_EVIDENCE,
    ResultStatus.PASS,
    ResultStatus.NOT_APPLICABLE,
)


@dataclass(frozen=True)
class MetricAggregate:
    evaluation_key: str
    numerator: int
    denominator: int
    not_applicable: int
    insufficient_evidence: int
    evaluator_error: int
    invalid: int
    invalid_reasons: Mapping[str, int] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def total(self) -> int:
        """Every result counted, so each one is accounted for in the report."""
        return (
            self.denominator
            + self.not_applicable
            + self.insufficient_evidence
            + self.evaluator_error
            + self.invalid
        )


def _aggregate_metric(key: str, results: list[EvaluationResult]) -> MetricAggregate:
    counts = Counter(result.status for result in results)
    return MetricAggregate(
        evaluation_key=key,
        numerator=counts[ResultStatus.PASS],
        denominator=counts[ResultStatus.PASS] + counts[ResultStatus.FAIL],
        not_applicable=counts[ResultStatus.NOT_APPLICABLE],
        insufficient_evidence=counts[ResultStatus.INSUFFICIENT_EVIDENCE],
        evaluator_error=counts[ResultStatus.EVALUATOR_ERROR],
        invalid=counts[ResultStatus.INVALID],
        invalid_reasons=dict(
            sorted(Counter(r.invalid_reason for r in results if r.invalid_reason).items())
        ),
    )


def aggregate(results: Iterable[EvaluationResult]) -> dict[str, MetricAggregate]:
    """Aggregate results per ``evaluation_key``, sorted by key.

    Raises ``ValueError`` when one unit has two results, because a repeat would
    inflate the denominator without any visible sign.
    """
    by_key: dict[str, list[EvaluationResult]] = {}
    seen: set[tuple[str, str, str | None]] = set()
    for result in results:
        unit = (result.evaluation_key, result.simulation_id, result.target)
        if unit in seen:
            raise ValueError(f"more than one result for unit {unit}")
        seen.add(unit)
        by_key.setdefault(result.evaluation_key, []).append(result)
    return {key: _aggregate_metric(key, by_key[key]) for key in sorted(by_key)}


def roll_up(statuses: Iterable[ResultStatus]) -> ResultStatus:
    """Roll several results into one per simulation.

    ``FAIL`` beats ``EVALUATOR_ERROR``, which beats ``INSUFFICIENT_EVIDENCE``,
    which beats ``PASS``. ``NOT_APPLICABLE`` counts only when every result is
    ``NOT_APPLICABLE``. ``INVALID`` sits just below ``FAIL``, because in an
    invalid run only a proved violation stays a ``FAIL``.
    """
    present = {ResultStatus(status) for status in statuses}
    for status in _ROLL_UP_ORDER:
        if status in present:
            return status
    raise ValueError("roll_up needs at least one status")
