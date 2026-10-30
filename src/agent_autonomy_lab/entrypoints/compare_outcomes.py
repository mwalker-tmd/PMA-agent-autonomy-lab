"""Compare the outcomes of runs of one task, field by field.

This is the behavioral-equivalence check (docs/ARCHITECTURE.md,
"Deterministic replay"). Give it the save directories (or ``outcome.json``
files) of two or more runs; the first is the reference:

    uv run python -m agent_autonomy_lab.entrypoints.compare_outcomes \\
        ../tau2-bench/data/simulations/m3-07-replay-off \\
        ../tau2-bench/data/simulations/m3-07-replay-on

It prints a JSON report: for each run, every compared field
(``domain.equivalence.COMPARED_FIELDS``) and whether it equals the
reference's, plus the reference values that are short enough to read. It
exits 0 only when every run equals the reference in every field.
"""

import argparse
import json
import sys
from pathlib import Path

from agent_autonomy_lab.domain.equivalence import COMPARED_FIELDS, RunOutcome


def _outcome_file(path: Path) -> Path:
    return path / "outcome.json" if path.is_dir() else path


def load(path: Path) -> tuple[dict, RunOutcome]:
    """The provenance fields and the outcome of one run's ``outcome.json``."""
    data = json.loads(_outcome_file(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if k != "outcome"}, RunOutcome.from_dict(data["outcome"])


def report(runs: list[tuple[str, dict, RunOutcome]]) -> dict:
    """Compare every run with the first; ``equivalent`` is true when no field differs."""
    (ref_name, _, reference), others = runs[0], runs[1:]
    comparisons = []
    for name, provenance, outcome in others:
        differing = reference.differences(outcome)
        comparisons.append(
            {
                "run": name,
                "provenance": provenance,
                "fields": {field: field not in differing for field in COMPARED_FIELDS},
                "differing": differing,
            }
        )
    return {
        "reference": {"run": ref_name, "provenance": runs[0][1]},
        "reference_values": {
            "messages": len(reference.transcript),
            "agent_messages": len(reference.agent_messages),
            "tool_calls": [call["name"] for call in reference.tool_calls],
            "final_db_hash": reference.final_db_hash,
            "termination_reason": reference.termination_reason,
            "reward": reference.reward,
            "reward_breakdown": reference.reward_breakdown,
        },
        "comparisons": comparisons,
        "equivalent": all(not c["differing"] for c in comparisons),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="+", type=Path, help="save directories or outcome.json files")
    args = parser.parse_args(argv)
    if len(args.runs) < 2:
        parser.error("give at least two runs")
    runs = [(str(path), *load(path)) for path in args.runs]
    result = report(runs)
    print(json.dumps(result, indent=2))
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    sys.exit(main())
