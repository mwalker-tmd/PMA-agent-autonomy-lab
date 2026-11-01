"""Report tool correctness and τ-bench's reward on saved runs, kept apart.

No model is called and nothing is sent. Give it save directories, or ``results.json``
or ``simulation.json`` files:

    uv run python -m agent_autonomy_lab.entrypoints.check_tool_correctness \\
        ../tau2-bench/data/simulations/m2-baseline-2026-10-27-r3

It prints a JSON report. For each simulation, ``benchmark`` holds τ-bench's result as
τ-bench gave it, ``tool_correctness`` holds the two parts of the lab's method, and
``checks`` and ``concerns`` hold the lab's results. No score combines them. It exits 1
if a lab check did not pass or apply; a reward below ``1.0`` is reported, and does not
change the exit code.
"""

import argparse
import json
import sys
from collections.abc import Sequence

from agent_autonomy_lab.adapters.tau2.reference_writes import GoldReplayReferenceWrites
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import (
    AuthenticationOrderEvaluator,
    MessageFormEvaluator,
    UserAccessEvaluator,
)
from agent_autonomy_lab.application.task_success import (
    TASK_SUCCESS_KEY,
    TaskSuccessEvaluator,
    benchmark_report,
)
from agent_autonomy_lab.application.tool_correctness import (
    EXPECTED_WRITES_KEY,
    ExpectedWritesEvaluator,
    prohibited_action_evaluators,
    prohibited_actions,
)
from agent_autonomy_lab.application.write_eligibility import (
    OneReplayPerSimulation,
    write_eligibility_evaluators,
)
from agent_autonomy_lab.domain.evaluation import ResultStatus
from agent_autonomy_lab.entrypoints.check_access import checked_simulations
from agent_autonomy_lab.ports.reference_writes import ReferenceWriteSource
from agent_autonomy_lab.ports.retail_state import RetailStateSource


def _status(results, key: str) -> ResultStatus:
    return next(r.status for r in results if r.evaluation_key == key)


def report(
    runs: Sequence[str], state_source: RetailStateSource, reference_source: ReferenceWriteSource
) -> list[dict]:
    state_source = OneReplayPerSimulation(state_source)
    evaluators = (
        AuthenticationOrderEvaluator(),
        UserAccessEvaluator(state_source),
        MessageFormEvaluator(),
        *write_eligibility_evaluators(state_source),
        *prohibited_action_evaluators(state_source, reference_source),
        ExpectedWritesEvaluator(reference_source),
        TaskSuccessEvaluator(),
    )
    rows = []
    for row, results, simulation in checked_simulations(runs, evaluators):
        checks = {key: value for key, value in row["checks"].items() if key != TASK_SUCCESS_KEY}
        rows.append(
            {
                "run": row["run"],
                "simulation_id": row["simulation_id"],
                "task_id": row["task_id"],
                "benchmark": {
                    "task_success": _status(results, TASK_SUCCESS_KEY).value,
                    **benchmark_report(simulation),
                },
                "tool_correctness": {
                    "prohibited_actions": prohibited_actions(results)[row["simulation_id"]].value,
                    "expected_writes": _status(results, EXPECTED_WRITES_KEY).value,
                },
                "checks": checks,
                "concerns": [c for c in row["concerns"] if c["check"] != TASK_SUCCESS_KEY],
            }
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="save directories or result files")
    args = parser.parse_args(argv)
    rows = report(args.runs, ReplayedRetailState(), GoldReplayReferenceWrites())
    print(json.dumps(rows, indent=2))
    return 1 if any(row["concerns"] for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
