"""Run one τ-bench task live through the traced lab agent and send its thread to LangSmith.

This is the repeatable trace run script (hook A, docs/BASELINE.md). Run with:

    uv run --env-file .env python -m agent_autonomy_lab.entrypoints.trace_live_run --task-id 0

Every default matches the Milestone 2 baseline run (reports/m2-baseline/
manifest.yaml) except the agent: the run uses the registered lab agent, so it
is a ``custom_lab_experiment``, never the baseline. The model is called, so a
run costs money. τ-bench's ``SimulationRun`` and a JSON summary are written to
``$TAU2_DATA_DIR/simulations/<save-to>/``, outside the repository.
``--dry-run`` runs the task and builds the trace but sends nothing.

The run fails (exit 1) when any agent turn sent a different system prompt,
tool schemas, or model arguments than upstream's ``llm_agent`` would.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from langsmith import Client

from agent_autonomy_lab.adapters.langsmith import LangSmithTraceSink, TraceShapeError, shape_errors
from agent_autonomy_lab.adapters.tau2.live_run import (
    LiveRun,
    LiveRunSettings,
    register_traced_agent,
    run_traced_task,
)
from agent_autonomy_lab.application.trace_live_run import LiveTrace, record_live_run
from agent_autonomy_lab.domain.agent_turns import AgentFingerprint
from agent_autonomy_lab.domain.events import NO_GATE, ExperimentClass, TrafficType
from agent_autonomy_lab.entrypoints.settings import langsmith_project
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink

BASELINE_MODEL = "openai/responses/gpt-6-luna"
BASELINE_ARGS = '{"reasoning_effort": "medium"}'


def _json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"not JSON: {error}") from error
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task-id", default="0", help="retail task ID (default 0)")
    parser.add_argument("--agent-llm", default=BASELINE_MODEL)
    parser.add_argument("--user-llm", default=BASELINE_MODEL)
    parser.add_argument("--agent-llm-args", type=_json_object, default=BASELINE_ARGS)
    parser.add_argument("--user-llm-args", type=_json_object, default=BASELINE_ARGS)
    parser.add_argument("--seed", type=int, default=42, help="run seed, as for tau2 run")
    parser.add_argument(
        "--save-to",
        default=None,
        help="directory name under $TAU2_DATA_DIR/simulations (default m3-live-<UTC time>)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run and build the trace; send nothing"
    )
    return parser


def _summary(live: LiveRun, trace: LiveTrace, project: str, dry_run: bool) -> dict:
    recording = live.recording
    turns = recording.turns
    mismatched = {
        str(turn.turn_index): diff
        for turn in turns
        if (diff := turn.fingerprint.differences(live.reference))
    }
    simulation = live.simulation
    agent_call_ms = sum(turn.elapsed_ms for turn in turns)
    language = live.language_context
    return {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "langsmith_project": project,
        "sent": not dry_run,
        "labels": {
            "experiment_class": ExperimentClass.CUSTOM_LAB_EXPERIMENT.value,
            "traffic_type": TrafficType.SYNTHETIC.value,
            "autonomy_level": NO_GATE,
            "agent_version": live.agent_name,
        },
        "simulation_id": simulation.simulation_id,
        "thread_id": trace.thread_id,
        "root_run_id": str(trace.root_run_id),
        "task_id": simulation.task_id,
        "task_sha256": language.get("task_sha256"),
        "persona_id": (language.get("persona") or {}).get("persona_id"),
        "trial_seed": live.trial_seed,
        "policy_version": live.policy_version,
        "termination_reason": simulation.termination_reason,
        "reward": simulation.reward.reward if simulation.reward else None,
        "reward_breakdown": dict(simulation.reward.breakdown) if simulation.reward else None,
        "messages": {
            "simulation": len(simulation.messages),
            "recorded": len(recording.transcript),
            "match": [(m.role, m.content) for m in simulation.messages]
            == [(m.role, m.content) for m in recording.transcript],
        },
        "trace": {"spans": trace.spans, "feedback": trace.feedback, "agent_turns": len(turns)},
        "fingerprint": {
            "reference_llm_agent": _fingerprint(live.reference),
            "traced_agent_turns": len(turns),
            "mismatched_turns": mismatched,
            "match": bool(turns) and not mismatched,
        },
        "overhead": {
            "simulation_wall_ms": round(recording.elapsed_ms, 1),
            "agent_calls_ms": round(agent_call_ms, 1),
            "in_loop_recording_ms": round(recording.overhead_ms, 3),
            "in_loop_share_of_wall": round(recording.overhead_ms / recording.elapsed_ms, 6),
            "post_run_build_ms": round(trace.build_ms, 3),
            "post_run_flush_ms": round(trace.flush_ms, 1),
        },
    }


def _fingerprint(sent: AgentFingerprint) -> dict:
    return {
        "model_id": sent.model_id,
        "prompt_version": sent.prompt_version,
        "tool_schema_version": sent.tool_schema_version,
        "model_args_version": sent.model_args_version,
        "model_args": dict(sent.model_args),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.dry_run and not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    data_dir = os.environ.get("TAU2_DATA_DIR")
    if not data_dir:
        print("TAU2_DATA_DIR is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    save_to = args.save_to or f"m3-live-{datetime.now(UTC):%Y-%m-%dT%H%M%SZ}"
    out_dir = Path(data_dir) / "simulations" / save_to
    if out_dir.exists():
        print(f"{out_dir} already exists; choose another --save-to", file=sys.stderr)
        return 1
    settings = LiveRunSettings(
        task_id=args.task_id,
        agent_llm=args.agent_llm,
        user_llm=args.user_llm,
        agent_llm_args=args.agent_llm_args,
        user_llm_args=args.user_llm_args,
        seed=args.seed,
    )
    project = langsmith_project()
    live = run_traced_task(settings, register_traced_agent())
    out_dir.mkdir(parents=True)
    (out_dir / "simulation.json").write_text(live.simulation_json, encoding="utf-8")

    sink: TraceSink
    if args.dry_run:
        sink = InMemoryTraceSink()
    else:
        # Synchronous sends, so a run LangSmith rejects raises instead of only being logged.
        sink = LangSmithTraceSink(Client(auto_batch_tracing=False), project)
    try:
        trace = record_live_run(
            live.recording, live.simulation, sink, args.user_llm, live.policy_version
        )
    except TraceShapeError as error:
        print(f"nothing sent: {error}", file=sys.stderr)
        return 1
    if isinstance(sink, InMemoryTraceSink) and (errors := shape_errors(sink.events, sink.feedback)):
        print("\n".join(errors), file=sys.stderr)
        return 1

    summary = _summary(live, trace, project, args.dry_run)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"saved:    {out_dir}")
    if not summary["fingerprint"]["match"]:
        print("the traced agent did not send what llm_agent sends", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
