"""Run the authentication, access, and message-form checks on saved runs.

No model is called and nothing is sent. Give it save directories, or
``results.json`` or ``simulation.json`` files:

    uv run python -m agent_autonomy_lab.entrypoints.check_access \\
        ../tau2-bench/data/simulations/m2-baseline-2026-10-27-r3

It prints a JSON report: for each simulation, the status counts per check, every
result that is neither ``PASS`` nor ``NOT_APPLICABLE``, and the roll-up for
authentication compliance. It exits 1 if any check did not pass or apply.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator, Sequence

from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import (
    AuthenticationOrderEvaluator,
    MessageFormEvaluator,
    UserAccessEvaluator,
    authentication_compliance,
)
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.export_simulation import evidence_trace
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    ResultStatus,
)
from agent_autonomy_lab.domain.events import ExperimentClass, TraceEvent
from agent_autonomy_lab.domain.simulations import SavedRun, SavedSimulation
from agent_autonomy_lab.entrypoints.replay_state import saved_simulations
from agent_autonomy_lab.ports.evaluator import Evaluator
from agent_autonomy_lab.ports.retail_state import RetailStateSource

_NOT_A_CONCERN = (ResultStatus.PASS, ResultStatus.NOT_APPLICABLE)


def _events(run_name: str, simulation: SavedSimulation) -> tuple[TraceEvent, ...]:
    run = SavedRun(run_name, None, None, None, None, (simulation,))
    return evidence_trace(run, simulation, ExperimentClass.CUSTOM_LAB_EXPERIMENT)


def checked_simulations(
    runs: Sequence[str], evaluators: Sequence[Evaluator]
) -> Iterator[tuple[dict, tuple[EvaluationResult, ...]]]:
    """Each saved simulation's report row and results under ``evaluators``."""
    for run in runs:
        directory, simulations = saved_simulations(run)
        for simulation in simulations:
            subject = EvaluationSubject(
                simulation_id=simulation.simulation_id,
                simulation=simulation,
                events=_events(directory.name, simulation),
                scenario_id=simulation.task_id,
            )
            results = evaluate_simulation(subject, evaluators).results
            checks: dict[str, Counter] = {}
            for result in results:
                checks.setdefault(result.evaluation_key, Counter())[result.status.value] += 1
            row = {
                "run": directory.name,
                "simulation_id": simulation.simulation_id,
                "task_id": simulation.task_id,
                "checks": {key: dict(sorted(checks[key].items())) for key in sorted(checks)},
                "concerns": [
                    {"check": r.evaluation_key, "status": r.status.value, "reason": r.reason}
                    for r in results
                    if r.status not in _NOT_A_CONCERN
                ],
            }
            yield row, results


def report(runs: Sequence[str], source: RetailStateSource) -> list[dict]:
    evaluators = (
        AuthenticationOrderEvaluator(),
        UserAccessEvaluator(source),
        MessageFormEvaluator(),
    )
    return [
        {
            **row,
            "authentication_compliance": authentication_compliance(results)[
                row["simulation_id"]
            ].value,
        }
        for row, results in checked_simulations(runs, evaluators)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="save directories or result files")
    args = parser.parse_args(argv)
    rows = report(args.runs, ReplayedRetailState())
    print(json.dumps(rows, indent=2))
    return 1 if any(row["concerns"] for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
