"""The ``Evaluator`` port and its in-memory test double."""

from collections.abc import Mapping
from typing import Protocol

from agent_autonomy_lab.domain.evaluation import EvaluationSubject, EvaluatorOutput


class Evaluator(Protocol):
    """Scores one simulation for one metric.

    ``evaluate`` returns at least one result: a metric that does not apply says
    ``NOT_APPLICABLE`` rather than returning nothing, and what cannot be proved
    is ``INSUFFICIENT_EVIDENCE``. A ``FAIL`` states its ``failure_kind``. The
    evaluator does not apply the run's standing; ``evaluate_simulation`` does,
    and it turns a raised exception or an unusable output into an
    ``EVALUATOR_ERROR`` result.
    """

    @property
    def evaluation_key(self) -> str: ...

    @property
    def version(self) -> str: ...

    def evaluate(self, subject: EvaluationSubject) -> EvaluatorOutput: ...


class CannedEvaluator:
    """Returns prepared outputs by ``simulation_id`` and records what it was asked.

    An unknown simulation raises ``KeyError``, and ``error`` makes every call
    raise, so tests can exercise an evaluator that fails.
    """

    def __init__(
        self,
        evaluation_key: str,
        version: str,
        outputs: Mapping[str, EvaluatorOutput] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.evaluation_key = evaluation_key
        self.version = version
        self.outputs = dict(outputs or {})
        self.error = error
        self.subjects: list[EvaluationSubject] = []

    def evaluate(self, subject: EvaluationSubject) -> EvaluatorOutput:
        self.subjects.append(subject)
        if self.error is not None:
            raise self.error
        return self.outputs[subject.simulation_id]
