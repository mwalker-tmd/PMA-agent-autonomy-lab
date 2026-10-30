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
tool schemas, or model arguments than upstream's ``llm_agent`` would. When
τ-bench raises mid-run (for example a provider error), it returns no
simulation and no ID; the partial conversation is traced under a
lab-assigned ``unfinished-`` ID with an error root, and the run exits 1.

``--replay CASSETTE`` serves every model response from a cassette recorded by
``cassette_run record`` instead of calling the models: this is the
tracing-on run of the behavioral-equivalence check (docs/ARCHITECTURE.md,
"Deterministic replay"). The run's settings must equal the cassette's. A
request that differs from the recording fails the run, and nothing is
traced; so does a cassette left with unserved responses. A finished run
whose live database can be read (any retail run) also writes
``outcome.json`` for ``compare_outcomes``.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from agent_autonomy_lab.adapters.langsmith import (
    LangSmithTraceSink,
    TraceShapeError,
    langsmith_client,
    shape_errors,
)
from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    CassetteMismatch,
    ReplayTransport,
    file_sha256,
    replaying,
)
from agent_autonomy_lab.adapters.tau2.live_run import (
    LiveRun,
    LiveRunFailed,
    LiveRunFailure,
    register_traced_agent,
    run_traced_task,
)
from agent_autonomy_lab.application.conversation_trace import MIN_MESSAGES
from agent_autonomy_lab.application.trace_live_run import LiveTrace, record_live_run
from agent_autonomy_lab.domain.agent_turns import (
    UNFINISHED_PREFIX,
    AgentFingerprint,
    LiveRecording,
    UnfinishedSimulation,
)
from agent_autonomy_lab.domain.equivalence import RunOutcome
from agent_autonomy_lab.domain.events import NO_GATE, ExperimentClass, TrafficType, new_id
from agent_autonomy_lab.domain.simulations import SavedSimulation
from agent_autonomy_lab.entrypoints.run_options import (
    add_run_settings,
    run_settings,
    settings_mismatch,
    write_outcome,
)
from agent_autonomy_lab.entrypoints.settings import langsmith_project
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_run_settings(parser)
    parser.add_argument(
        "--save-to",
        default=None,
        help="directory name under $TAU2_DATA_DIR/simulations (default m3-live-<UTC time>)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run and build the trace; send nothing"
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="CASSETTE",
        help="serve model responses from this cassette instead of calling the models",
    )
    return parser


def _summary(
    recording: LiveRecording,
    reference: AgentFingerprint,
    agent_name: str,
    trace: LiveTrace,
    project: str,
    dry_run: bool,
) -> dict:
    """Summary fields shared by finished and unfinished runs."""
    turns = recording.turns
    mismatched = {
        str(turn.turn_index): {name: getattr(turn.fingerprint, name) for name in diff}
        for turn in turns
        if (diff := turn.fingerprint.differences(reference))
    }
    agent_call_ms = sum(turn.elapsed_ms for turn in turns)
    return {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "langsmith_project": project,
        "sent": not dry_run,
        "labels": {
            "experiment_class": ExperimentClass.CUSTOM_LAB_EXPERIMENT.value,
            "traffic_type": TrafficType.SYNTHETIC.value,
            "autonomy_level": NO_GATE,
            "agent_version": agent_name,
        },
        "simulation_id": trace.simulation_id,
        "thread_id": trace.thread_id,
        "root_run_id": str(trace.root_run_id),
        "trace": {"spans": trace.spans, "feedback": trace.feedback, "agent_turns": len(turns)},
        "fingerprint": {
            "reference_llm_agent": _fingerprint(reference),
            "traced_agent_turns": len(turns),
            "mismatched_turns": mismatched,
            "match": bool(turns) and not mismatched,
        },
        "overhead": {
            "simulation_wall_ms": round(recording.elapsed_ms, 1),
            "agent_calls_ms": round(agent_call_ms, 1),
            "in_loop_recording_ms": round(recording.overhead_ms, 3),
            "in_loop_share_of_wall": round(recording.overhead_ms / recording.elapsed_ms, 6)
            if recording.elapsed_ms
            else None,
            "post_run_build_ms": round(trace.build_ms, 3),
            "post_run_flush_ms": round(trace.flush_ms, 1),
        },
    }


def _finished_summary(live: LiveRun, trace: LiveTrace, project: str, dry_run: bool) -> dict:
    simulation, recording = live.simulation, live.recording
    language = live.language_context
    summary = _summary(recording, live.reference, live.agent_name, trace, project, dry_run)
    summary.update(
        {
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
        }
    )
    return summary


def _fingerprint(sent: AgentFingerprint) -> dict:
    return {
        "model_id": sent.model_id,
        "prompt_version": sent.prompt_version,
        "tool_schema_version": sent.tool_schema_version,
        "model_args_version": sent.model_args_version,
        "model_args": dict(sent.model_args),
    }


def _sink(dry_run: bool, project: str) -> TraceSink:
    if dry_run:
        return InMemoryTraceSink()
    # Synchronous sends, so a run LangSmith rejects raises instead of only being logged.
    return LangSmithTraceSink(langsmith_client(auto_batch_tracing=False), project)


def _record(
    recording: LiveRecording,
    simulation: SavedSimulation | UnfinishedSimulation,
    sink: TraceSink,
    user_model_id: str,
    policy_version: str,
) -> LiveTrace | None:
    """Record and send the trace; print why and return ``None`` if it is refused."""
    try:
        trace = record_live_run(recording, simulation, sink, user_model_id, policy_version)
    except TraceShapeError as error:
        print(f"nothing sent: {error}", file=sys.stderr)
        return None
    if isinstance(sink, InMemoryTraceSink) and (errors := shape_errors(sink.events, sink.feedback)):
        print("\n".join(errors), file=sys.stderr)
        return None
    return trace


def _write_summary(out_dir: Path, summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"saved:    {out_dir}")


def _trace_failure(
    failure: LiveRunFailure, args: argparse.Namespace, project: str, out_dir: Path
) -> int:
    """Trace what the agent recorded before the run raised, then report the exception."""
    cause = failure.cause
    traceback.print_exception(cause, file=sys.stderr)
    recording = failure.recording
    if recording is None or len(recording.transcript) < MIN_MESSAGES:
        print("the run failed before a conversation formed; nothing to trace", file=sys.stderr)
        return 1
    unfinished = UnfinishedSimulation(
        simulation_id=f"{UNFINISHED_PREFIX}{new_id()}",
        task_id=failure.task_id,
        error_class=type(cause).__name__,
        error_message=str(cause) or None,
    )
    trace = _record(
        recording, unfinished, _sink(args.dry_run, project), args.user_llm, failure.policy_version
    )
    if trace is None:
        return 1
    summary = _summary(
        recording, failure.reference, failure.agent_name, trace, project, args.dry_run
    )
    summary.update(
        {
            "task_id": failure.task_id,
            "trial_seed": failure.trial_seed,
            "policy_version": failure.policy_version,
            "unfinished": {
                "error_class": unfinished.error_class,
                "error_message": unfinished.error_message,
            },
            "messages": {"recorded": len(recording.transcript)},
        }
    )
    _write_summary(out_dir, summary)
    print(
        f"the run raised {unfinished.error_class}; its partial thread was traced", file=sys.stderr
    )
    return 1


def _run(args: argparse.Namespace, project: str, out_dir: Path) -> tuple[LiveRun | None, int, dict]:
    """Run the task live, or from ``--replay``.

    Returns the run (``None`` if it failed), the exit code for a failed
    run, and where the model responses came from.
    """
    settings = run_settings(args)
    if args.replay is None:
        try:
            live = run_traced_task(settings, register_traced_agent())
        except LiveRunFailed as failed:
            return None, _trace_failure(failed.failure, args, project, out_dir), {}
        return live, 0, {"mode": "live", "tracing": "on"}
    sha256 = file_sha256(args.replay)
    cassette = Cassette.load(args.replay)
    if reason := settings_mismatch(settings, cassette):
        print(reason, file=sys.stderr)
        return None, 1, {}
    with replaying(cassette) as transport:
        try:
            live = run_traced_task(settings, register_traced_agent())
        except LiveRunFailed as failed:
            # A replay that fails is a broken check, not a conversation to trace.
            traceback.print_exception(failed.failure.cause, file=sys.stderr)
            _report_replay(transport)
            return None, 1, {}
    if not _report_replay(transport):
        return None, 1, {}
    source = {"path": str(args.replay), "sha256": sha256, "served": transport.served}
    return live, 0, {"mode": "replay", "tracing": "on", "cassette": source}


def _report_replay(transport: ReplayTransport) -> bool:
    """Print why the replay did not match its cassette; ``True`` if it did."""
    try:
        transport.check_complete()
    except CassetteMismatch as mismatch:
        print(f"cassette: {mismatch}; nothing traced", file=sys.stderr)
        return False
    return True


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
    if args.replay is not None and not args.replay.is_file():
        print(f"{args.replay} is not a cassette file", file=sys.stderr)
        return 1
    project = langsmith_project()
    live, code, source = _run(args, project, out_dir)
    if live is None:
        return code
    out_dir.mkdir(parents=True)
    (out_dir / "simulation.json").write_text(live.simulation_json, encoding="utf-8")

    trace = _record(
        live.recording,
        live.simulation,
        _sink(args.dry_run, project),
        args.user_llm,
        live.policy_version,
    )
    if trace is None:
        return 1
    summary = _finished_summary(live, trace, project, args.dry_run)
    summary.update(source)
    _write_summary(out_dir, summary)
    if live.final_db_hash is not None:
        outcome = RunOutcome.of(live.simulation, live.final_db_hash)
        write_outcome(
            out_dir,
            outcome,
            {
                "agent_version": live.agent_name,
                **source,
                "thread_id": trace.thread_id,
                "root_run_id": str(trace.root_run_id),
            },
        )
    if not summary["fingerprint"]["match"]:
        print("the traced agent did not send what llm_agent sends", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
