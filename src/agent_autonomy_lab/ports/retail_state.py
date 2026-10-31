"""The ``RetailStateSource`` port and its in-memory test double."""

from collections.abc import Mapping
from typing import Protocol

from agent_autonomy_lab.domain.retail_state import RetailStateTimeline
from agent_autonomy_lab.domain.simulations import SavedSimulation


class RetailStateSource(Protocol):
    """Gives the retail database as it was at each write of a saved simulation."""

    def timeline(self, simulation: SavedSimulation) -> RetailStateTimeline: ...


class CannedRetailState:
    """Returns prepared timelines by ``simulation_id`` and records what it was asked.

    An unknown simulation raises ``KeyError``, so a test notices a state it did
    not prepare.
    """

    def __init__(self, timelines: Mapping[str, RetailStateTimeline] | None = None) -> None:
        self.timelines = dict(timelines or {})
        self.simulations: list[SavedSimulation] = []

    def timeline(self, simulation: SavedSimulation) -> RetailStateTimeline:
        self.simulations.append(simulation)
        return self.timelines[simulation.simulation_id]
