"""What a batch run is made of: its plan, its attempts, and what it spent.

Spend is computed at list price from the usage each provider response reports, because
LiteLLM's estimate was about a third of the Milestone 2 bill. A call that cannot be priced
is counted rather than treated as free, so a run can stop when it can no longer hold its cap.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_autonomy_lab.domain.agent_turns import (
    AgentFingerprint,
    LiveRecording,
    UnfinishedSimulation,
)
from agent_autonomy_lab.domain.simulations import SavedSimulation


@dataclass(frozen=True)
class TaskList:
    """A versioned list of tasks: which file and set it came from, and the file's hash."""

    version: str
    set_name: str
    task_ids: tuple[str, ...]
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ids", tuple(self.task_ids))
        if not self.task_ids:
            raise ValueError("a task list needs at least one task")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("a task list names a task twice")


@dataclass(frozen=True)
class BatchPlan:
    """Which simulations to run, and the limits on running them."""

    task_list: TaskList
    trials: int
    cap_usd: float
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError("a batch needs at least one trial")
        if self.cap_usd <= 0:
            raise ValueError("a batch needs a positive spend cap")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")

    def units(self) -> tuple[tuple[str, int], ...]:
        """Every (task, trial), by trial and then in the list's order.

        Whole trials come first, so a run stopped early leaves each task with a similar
        number of trials rather than some tasks with all of them and others with none.
        """
        return tuple(
            (task_id, trial) for trial in range(self.trials) for task_id in self.task_list.task_ids
        )


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class ProviderCall:
    """One response a provider returned, read from the HTTP exchange.

    ``input_tokens`` and ``output_tokens`` are ``None`` when the response reports no usage.
    """

    model: str
    status: int
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.status < 400

    @property
    def is_unpriced_usage_gap(self) -> bool:
        """A successful response is billed, so one that reports no usage cannot be priced."""
        return self.succeeded and (self.input_tokens is None or self.output_tokens is None)


class SpendLedger:
    """Totals per model at list price; a refused response reports no usage and costs nothing."""

    def __init__(self, prices: Mapping[str, ModelPrice]) -> None:
        self._prices = dict(prices)
        self._by_model: dict[str, dict[str, Any]] = {}
        self.unpriced_calls = 0

    def add(self, calls: Iterable[ProviderCall]) -> None:
        for call in calls:
            row = self._by_model.setdefault(
                call.model,
                {"calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0},
            )
            row["calls"] += 1
            row["failed_calls"] += 0 if call.succeeded else 1
            row["input_tokens"] += call.input_tokens or 0
            row["output_tokens"] += call.output_tokens or 0
            price = self._prices.get(call.model)
            if call.is_unpriced_usage_gap or (call.succeeded and price is None):
                self.unpriced_calls += 1
            elif price is not None:
                row["usd"] += (
                    (call.input_tokens or 0) * price.input_per_million
                    + (call.output_tokens or 0) * price.output_per_million
                ) / 1_000_000

    @property
    def total_usd(self) -> float:
        return sum(row["usd"] for row in self._by_model.values())

    def by_model(self) -> dict[str, dict[str, Any]]:
        return {model: dict(self._by_model[model]) for model in sorted(self._by_model)}


@dataclass(frozen=True)
class SimulationAttempt:
    """One try at one trial of one task, which either finished or raised.

    ``unfinished`` is set when the run raised and τ-bench returned no simulation.
    ``provider_calls`` is what reached a provider. ``callback_calls`` is what the LiteLLM
    callback saw of the same run (hook C), kept apart so one can be checked against the other.
    ``effective_settings`` is what the requests carried and the responses reported, per role.
    """

    task_id: str
    trial: int
    attempt: int
    trial_seed: int
    reference: AgentFingerprint
    policy_version: str
    agent_name: str
    recording: LiveRecording | None = None
    simulation: SavedSimulation | None = None
    unfinished: UnfinishedSimulation | None = None
    provider_calls: tuple[ProviderCall, ...] = ()
    callback_calls: tuple[Mapping[str, Any], ...] = ()
    effective_settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.simulation is None) == (self.unfinished is None):
            raise ValueError("an attempt either finished or raised")

    @property
    def simulation_id(self) -> str:
        return (self.simulation or self.unfinished).simulation_id

    @property
    def fingerprint_differences(self) -> dict[str, dict[str, Any]]:
        """Per agent turn that differs from upstream's agent, the values that turn sent."""
        turns = self.recording.turns if self.recording else ()
        return {
            str(turn.turn_index): {name: getattr(turn.fingerprint, name) for name in names}
            for turn in turns
            if (names := turn.fingerprint.differences(self.reference))
        }
