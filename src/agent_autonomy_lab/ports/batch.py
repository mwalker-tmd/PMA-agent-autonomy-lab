"""The ports a batch run needs, and their in-memory test doubles."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_autonomy_lab.domain.batch import ProviderCall, SimulationAttempt
from agent_autonomy_lab.domain.evaluation import EvaluationSubject, EvaluatorOutput


class StageFailed(Exception):
    """A run or its scoring raised after model calls that still have to be paid for."""

    def __init__(self, cause: Exception, provider_calls: tuple[ProviderCall, ...]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.provider_calls = provider_calls


class SimulationRunner(Protocol):
    """Runs one trial of one task once.

    A run that raised comes back as an attempt with ``unfinished`` set. Raising here means
    the runner itself cannot go on, and it stops the batch. ``StageFailed`` carries any
    model calls already made, so the spend still counts.
    """

    def run(self, task_id: str, trial: int, attempt: int) -> SimulationAttempt: ...


@dataclass(frozen=True)
class ScoredSimulation:
    """Every evaluator's output on one simulation, and the model calls that took."""

    output: EvaluatorOutput
    provider_calls: tuple[ProviderCall, ...] = ()
    callback_calls: tuple[Mapping[str, Any], ...] = field(default=())


class SimulationScorer(Protocol):
    """Scores one simulation with every evaluator; it never samples.

    Raising stops the batch, and ``StageFailed`` carries any model calls already made.
    """

    def score(self, subject: EvaluationSubject) -> ScoredSimulation: ...


class ScriptedSimulationRunner:
    """Returns prepared attempts in order; an exception in the script is raised."""

    def __init__(self, attempts: Iterable[SimulationAttempt | Exception]) -> None:
        self._attempts = iter(attempts)
        self.asked: list[tuple[str, int, int]] = []

    def run(self, task_id: str, trial: int, attempt: int) -> SimulationAttempt:
        self.asked.append((task_id, trial, attempt))
        scripted = next(self._attempts)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


class ScriptedSimulationScorer:
    """Scores through a function, and records what it was asked to score."""

    def __init__(self, score: Callable[[EvaluationSubject], ScoredSimulation]) -> None:
        self._score = score
        self.subjects: list[EvaluationSubject] = []

    def score(self, subject: EvaluationSubject) -> ScoredSimulation:
        self.subjects.append(subject)
        return self._score(subject)
