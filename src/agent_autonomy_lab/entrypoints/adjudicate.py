"""Prepare the adjudication sample and the blind worksheet (#110).

``sample`` picks the cases and writes them with their strata, and the worksheet page goes
to ``--worksheet``. No model call is made:

    uv run python -m agent_autonomy_lab.entrypoints.adjudicate sample \\
        ../tau2-bench/data/simulations/m4-baseline \\
        --sample sample.yaml --worksheet worksheet.html

Keep the sample file away from the reviewer until the labels are in, because its strata say
what the checks found.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import yaml

from agent_autonomy_lab.application.adjudication import (
    SAMPLE_SEED,
    SAMPLE_SIZE,
    executed_a_write,
    select_sample,
    worksheet_case,
    worksheet_data,
)
from agent_autonomy_lab.domain.simulations import Message
from agent_autonomy_lab.entrypoints.replay_state import saved_simulations

REPORT = Path("reports/m4-baseline/simulations.jsonl")
TASK_SETS = Path("datasets/m4-task-sets-v1.json")


def saved_messages(save_dir: Path, row: dict) -> tuple[Message, ...]:
    """The saved conversation of one row of the batch report."""
    name = f"task-{row['task_id']}-trial-{row['trial']}-attempt-{row['attempt']}"
    _, simulations = saved_simulations(str(save_dir / name / "simulation.json"))
    (simulation,) = simulations
    return simulation.messages


def render_worksheet(cases: Sequence[dict]) -> str:
    page = resources.files("agent_autonomy_lab.entrypoints") / "adjudication_worksheet.html"
    return page.read_text(encoding="utf-8").replace("__WORKSHEET_DATA__", worksheet_data(cases))


def sample(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in args.report.read_text(encoding="utf-8").splitlines()]
    tasks = {t["id"]: t for t in json.loads(args.task_sets.read_text("utf-8"))["baseline"]["tasks"]}
    messages = {row["simulation_id"]: saved_messages(args.save_dir, row) for row in rows}
    wrote = {key: executed_a_write(value) for key, value in messages.items()}
    cases = select_sample(rows, tasks, wrote)
    page = render_worksheet(
        [worksheet_case(case.case_id, messages[case.simulation_id]) for case in cases]
    )
    args.worksheet.write_text(page, encoding="utf-8")
    record = {
        "issue": 110,
        "report": str(args.report),
        "size": SAMPLE_SIZE,
        "seed": SAMPLE_SEED,
        "worksheet_sha256": hashlib.sha256(page.encode("utf-8")).hexdigest(),
        "cases": [
            {
                "case_id": case.case_id,
                "task_id": case.task_id,
                "trial": case.trial,
                "simulation_id": case.simulation_id,
                "strata": list(case.strata),
            }
            for case in cases
        ],
    }
    text = yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=100)
    args.sample.write_text(text, encoding="utf-8")
    print(f"{len(cases)} cases written; strata are in {args.sample}, not printed here.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("sample", help="choose the cases and write the worksheet")
    pick.add_argument("save_dir", type=Path, help="the baseline's save directory")
    pick.add_argument("--sample", type=Path, required=True)
    pick.add_argument("--worksheet", type=Path, required=True)
    pick.add_argument("--report", type=Path, default=REPORT)
    pick.add_argument("--task-sets", type=Path, default=TASK_SETS)
    pick.set_defaults(run=sample)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
