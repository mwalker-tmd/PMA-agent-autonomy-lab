"""The retail database as it was at points in one simulation.

τ-bench does not save a run's database, and the agent's own reads do not cover
everything a rule needs, such as a gift-card balance it never looked up. A
``RetailStateTimeline`` holds what a replay of the run's writes shows
(docs/EVALUATION.md, "Point-in-time state"). A replay that stops matching the
record cannot vouch for later state, so that state is ``None``, not a guess: an
evaluator that gets ``None`` reports ``INSUFFICIENT_EVIDENCE``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_autonomy_lab.domain.events import json_data


class WriteOutcome(StrEnum):
    """What the run's record says happened to a write attempt."""

    EXECUTED = "executed"
    FAILED = "failed"  # the tool ran and returned an error; it may still have changed state
    REJECTED = "rejected"  # never reached the tool
    NO_RESULT = "no_result"  # the record has no result, so whether it ran is unknown
    AMBIGUOUS = "ambiguous"  # the record has several results, so whether it ran is unknown


@dataclass(frozen=True)
class RetailState:
    """A detached copy of the database at one point; changing it changes nothing else."""

    db_hash: str
    orders: Mapping[str, Any] = field(repr=False)
    users: Mapping[str, Any] = field(repr=False)
    products: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.db_hash:
            raise ValueError("a state needs a db_hash")
        for name in ("orders", "users", "products"):
            object.__setattr__(self, name, json_data(getattr(self, name)))


@dataclass(frozen=True)
class WriteAttempt:
    """One write the agent asked for; ``state_before`` is ``None`` if it cannot be vouched for."""

    call_id: str
    tool_name: str
    outcome: WriteOutcome
    state_before: RetailState | None

    def __post_init__(self) -> None:
        if not self.call_id or not self.tool_name:
            raise ValueError("a write attempt needs a call_id and a tool_name")
        object.__setattr__(self, "outcome", WriteOutcome(self.outcome))


@dataclass(frozen=True)
class ReplayDivergence:
    """A point where the replay stopped matching the run's record."""

    call_id: str
    tool_name: str
    reason: str


@dataclass(frozen=True)
class RetailStateTimeline:
    """The state before each write attempt and at the end of one simulation.

    ``final`` is ``None`` when the replay diverged, because it then cannot vouch
    for the end state. ``replayed_hash`` is always the hash the replay reached,
    so a difference from a live hash can be seen and explained.
    """

    simulation_id: str
    writes: tuple[WriteAttempt, ...]
    final: RetailState | None
    replayed_hash: str
    status_reads_checked: int = 0
    divergences: tuple[ReplayDivergence, ...] = ()

    def __post_init__(self) -> None:
        if not self.simulation_id or not self.replayed_hash:
            raise ValueError("a timeline needs a simulation_id and a replayed_hash")
        object.__setattr__(self, "writes", tuple(self.writes))
        object.__setattr__(self, "divergences", tuple(self.divergences))
        if (self.final is None) != bool(self.divergences):
            raise ValueError("final must be None exactly when the replay diverged")
        if self.final is not None and self.final.db_hash != self.replayed_hash:
            raise ValueError("final does not match replayed_hash")

    def before(self, call_id: str) -> RetailState | None:
        """The state before this write, or ``None`` if it is unknown or the ID is ambiguous."""
        matches = [write for write in self.writes if write.call_id == call_id]
        return matches[0].state_before if len(matches) == 1 else None
