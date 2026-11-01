"""Run the write-eligibility checks on saved runs.

No model is called and nothing is sent. Give it save directories, or
``results.json`` or ``simulation.json`` files:

    uv run python -m agent_autonomy_lab.entrypoints.check_writes \\
        ../tau2-bench/data/simulations/m2-baseline-2026-10-27-r3

It prints a JSON report: for each simulation, the status counts per check and
every result that is neither ``PASS`` nor ``NOT_APPLICABLE``. It exits 1 if any
check did not pass or apply.
"""

import argparse
import json
import sys
from collections.abc import Sequence

from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.write_eligibility import write_eligibility_evaluators
from agent_autonomy_lab.entrypoints.check_access import checked_simulations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="save directories or result files")
    args = parser.parse_args(argv)
    evaluators = write_eligibility_evaluators(ReplayedRetailState())
    rows = [row for row, *_ in checked_simulations(args.runs, evaluators)]
    print(json.dumps(rows, indent=2))
    return 1 if any(row["concerns"] for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
