"""Replay saved runs' writes offline and compare the end state with the live hash.

No model is called and nothing is sent. Give it save directories, or
``results.json`` or ``simulation.json`` files:

    uv run python -m agent_autonomy_lab.entrypoints.replay_state \\
        ../tau2-bench/data/simulations/m3-07-record

It prints a JSON report: for each simulation, its writes, the status reads it
checked, any divergence, the replayed hash, and the live hash when the run's
``outcome.json`` recorded one. It exits 1 if a replay diverged or a hash differs.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_autonomy_lab.adapters.tau2.results_reader import (
    read_results,
    results_path,
    simulation_from_dict,
)
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.domain.simulations import SavedSimulation

# Replay does not read message times, so the zone they were written in does not matter.
_ANY_ZONE = ZoneInfo("UTC")


def _simulations(run: str) -> tuple[Path, tuple[SavedSimulation, ...]]:
    path = Path(run).expanduser()
    single = path if path.name == "simulation.json" else path / "simulation.json"
    if single.is_file():
        data = json.loads(single.read_text(encoding="utf-8"))
        return single.parent, (simulation_from_dict(data, _ANY_ZONE),)
    results = results_path(run)
    return results.parent, read_results(results, _ANY_ZONE).simulations


def _live_hash(directory: Path) -> str | None:
    outcome = directory / "outcome.json"
    if not outcome.is_file():
        return None
    return json.loads(outcome.read_text(encoding="utf-8"))["outcome"]["final_db_hash"]


def report(runs: Sequence[str], source: ReplayedRetailState) -> list[dict]:
    rows = []
    for run in runs:
        directory, simulations = _simulations(run)
        live_hash = _live_hash(directory)
        for simulation in simulations:
            timeline = source.timeline(simulation)
            rows.append(
                {
                    "run": directory.name,
                    "simulation_id": simulation.simulation_id,
                    "task_id": simulation.task_id,
                    "writes": [
                        {"tool": write.tool_name, "outcome": write.outcome.value}
                        for write in timeline.writes
                    ],
                    "status_reads_checked": timeline.status_reads_checked,
                    "divergences": [
                        {"call_id": d.call_id, "tool": d.tool_name, "reason": d.reason}
                        for d in timeline.divergences
                    ],
                    "replayed_hash": timeline.replayed_hash,
                    "live_hash": live_hash,
                    "hash_matches_live": (
                        None if live_hash is None else live_hash == timeline.replayed_hash
                    ),
                }
            )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="save directories or result files")
    args = parser.parse_args(argv)
    rows = report(args.runs, ReplayedRetailState())
    print(json.dumps(rows, indent=2))
    clean = all(not r["divergences"] and r["hash_matches_live"] is not False for r in rows)
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
