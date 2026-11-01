"""Run the confirmation check on saved runs, and optionally record its model calls.

This makes one live model call per write that passes the state precondition. Give it
save directories, or ``results.json`` or ``simulation.json`` files:

    uv run --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.judge_confirmation \\
        ../tau2-bench/data/simulations/m2-baseline-2026-10-27-r3

It prints a JSON report for ``reports/m4-confirmation-judge/manifest.yaml``: each write's
result and the tool span it is bound to, each simulation's roll-up, and the total usage and
cost. It exits 1 if a judge gave ``EVALUATOR_ERROR``. A ``FAIL`` does not change the exit
code. ``--recording`` saves the calls for replay, and an existing path is refused before
any model call. ``tests/test_confirmation_judge.py`` replays the committed one.
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
from agent_autonomy_lab.application.confirmation_judge import ConfirmationEvaluator
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    ResultStatus,
    roll_up,
)
from agent_autonomy_lab.domain.events import EventType
from agent_autonomy_lab.entrypoints.check_access import saved_subjects
from agent_autonomy_lab.entrypoints.judge_rubrics import CallUsage
from agent_autonomy_lab.ports.model_client import ModelClient


def bound_span(result: EvaluationResult, subject: EvaluationSubject) -> dict | None:
    """The tool span a result cites, with what binds it to the write; ``None`` if none."""
    cited = (event for event in subject.events if event.event_id in result.evidence_ids)
    span = next((event for event in cited if event.event_type is EventType.TOOL_CALL), None)
    if span is None:
        return None
    return {
        "tool": span.tool_name,
        "run_id": str(span.run_id),
        "turn_index": span.turn_index,
        "argument_hash": span.argument_hash,
    }


def report(runs: Sequence[str], client: ModelClient) -> dict:
    evaluator = ConfirmationEvaluator(client)
    usage = CallUsage()
    statuses: Counter = Counter()
    rows = []
    for run_name, subject in saved_subjects(runs):
        output = evaluate_simulation(subject, (evaluator,))
        usage.add(raw.payload["call"] for raw in output.raw_outputs if "call" in raw.payload)
        statuses.update(result.status.value for result in output.results)
        rows.append(
            {
                "run": run_name,
                "simulation_id": subject.simulation_id,
                "task_id": subject.scenario_id,
                "confirmation": roll_up(result.status for result in output.results).value,
                "results": [
                    {
                        "status": result.status.value,
                        "score": result.score,
                        "failure_kind": result.failure_kind.value if result.failure_kind else None,
                        "target": result.target,
                        "bound_span": bound_span(result, subject),
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
            **usage.as_dict(),
            "results": dict(sorted(statuses.items())),
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
        result["recording_sha256"] = recorder.cassette({"judge_confirmation": True}).save(
            args.recording
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if ResultStatus.EVALUATOR_ERROR.value in result["totals"]["results"] else 0


if __name__ == "__main__":
    sys.exit(main())
