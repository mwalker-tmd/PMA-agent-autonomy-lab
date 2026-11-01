"""Run the three rubric judges on saved runs, and optionally record their model calls.

This calls the judge model three times per simulation (live; about a quarter of a cent per
simulation at the list price). Give it save directories, or ``results.json`` or
``simulation.json`` files:

    uv run --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.judge_rubrics \\
        ../tau2-bench/data/simulations/m2-baseline-2026-10-27-r3

It prints a JSON report for ``reports/m4-rubric-judges/manifest.yaml``: each simulation's
results, the replies whose wrapper was removed, and the total usage and cost, which is
null if any call's cost is unknown, as for one the adapter cannot price or one that failed. It
exits 1 if a judge gave ``EVALUATOR_ERROR``. A low score does not change the exit code.
``--recording`` saves the calls for replay, and an existing path is refused before any
model call. ``tests/test_rubric_judges.py`` replays the committed one.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

from agent_autonomy_lab.adapters.litellm import RecordingTransport, litellm_transport
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.rubric_judges import rubric_judges
from agent_autonomy_lab.domain.evaluation import ResultStatus
from agent_autonomy_lab.entrypoints.check_access import saved_subjects
from agent_autonomy_lab.ports.model_client import ModelClient


def report(runs: Sequence[str], client: ModelClient) -> dict:
    judges = rubric_judges(client)
    rows = []
    totals = {"calls": 0, "calls_of_unknown_cost": 0, "input_tokens": 0, "output_tokens": 0}
    known_cost = 0.0
    statuses: dict[str, Counter] = {}
    for run_name, subject in saved_subjects(runs):
        output = evaluate_simulation(subject, judges)
        calls = [raw.payload["call"] for raw in output.raw_outputs if "call" in raw.payload]
        for call in calls:
            totals["calls"] += 1
            totals["input_tokens"] += call.get("input_tokens", 0)
            totals["output_tokens"] += call.get("output_tokens", 0)
            if call.get("cost_usd") is None:
                totals["calls_of_unknown_cost"] += 1
            else:
                known_cost += call["cost_usd"]
        for result in output.results:
            statuses.setdefault(result.evaluation_key, Counter())[result.status.value] += 1
        rows.append(
            {
                "run": run_name,
                "simulation_id": subject.simulation_id,
                "task_id": subject.scenario_id,
                "unwrapped_replies": calls[0]["unwrapped_replies"] if calls else 0,
                "results": [
                    {
                        "evaluation_key": result.evaluation_key,
                        "status": result.status.value,
                        "score": result.score,
                        "failure_kind": result.failure_kind.value if result.failure_kind else None,
                        "reason": result.reason,
                        "judge_run": result.judge_run.to_dict() if result.judge_run else None,
                    }
                    for result in output.results
                ],
            }
        )
    return {
        "totals": {
            "simulations": len(rows),
            **totals,
            "cost_usd": None if totals["calls_of_unknown_cost"] else known_cost,
            "results_by_judge": {key: dict(sorted(statuses[key].items())) for key in statuses},
        },
        "simulations": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="save directories or result files")
    parser.add_argument("--recording", type=Path, help="save the model calls here for replay")
    args = parser.parse_args(argv)
    if args.recording and args.recording.exists():
        parser.error(f"{args.recording} exists; remove it to record again")

    recorder = RecordingTransport() if args.recording else None
    with litellm_transport(recorder) if recorder else nullcontext():
        result = report(args.runs, LiteLLMModelClient())
    if recorder:
        result["recording_sha256"] = recorder.cassette({"judge_rubrics": True}).save(args.recording)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    errors = result["totals"]["results_by_judge"]
    return 1 if any(ResultStatus.EVALUATOR_ERROR.value in row for row in errors.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
