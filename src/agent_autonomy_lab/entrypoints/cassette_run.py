"""Record one live τ-bench task as a cassette, or replay it with tracing off.

Behavioral equivalence (docs/ARCHITECTURE.md, "Deterministic replay") needs
runs that differ only in whether tracing is on. Reasoning models do not
repeat a conversation from a seed, so one live run is recorded, and both
compared runs replay its model responses:

    # live; calls the models, so it costs money
    uv run --env-file .env --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.cassette_run record \\
        --cassette cassettes/m3-07-task-0.cassette.json --save-to m3-07-record

    # tracing off: upstream's agent, nothing recorded or sent
    uv run --env-file .env python -m agent_autonomy_lab.entrypoints.cassette_run replay \\
        --cassette cassettes/m3-07-task-0.cassette.json --save-to m3-07-replay-off

The tracing-on replay is ``trace_live_run --replay``. Both commands run
upstream's ``LLMAgent`` unwrapped (registered as ``lab_untraced_llm_agent``
so the run's live database can be read). Every default matches the
Milestone 2 baseline; a replay's settings must equal the cassette's. Each run
writes ``simulation.json`` and ``outcome.json`` (what ``compare_outcomes``
compares) to ``$TAU2_DATA_DIR/simulations/<save-to>/``. The cassette is
written once and never overwritten; ``cassettes/`` is ignored by Git.
"""

import argparse
import os
import sys
from pathlib import Path

from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    CassetteMismatch,
    RecordingTransport,
    file_sha256,
    litellm_transport,
    replaying,
)
from agent_autonomy_lab.adapters.tau2.live_run import (
    LiveRunSettings,
    TaskRun,
    register_untraced_agent,
    run_untraced_task,
)
from agent_autonomy_lab.domain.equivalence import RunOutcome
from agent_autonomy_lab.entrypoints.run_options import (
    add_run_settings,
    run_settings,
    settings_data,
    settings_mismatch,
    write_outcome,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="run live and record every model response")
    replay = commands.add_parser("replay", help="replay a cassette with tracing off")
    for command in (record, replay):
        command.add_argument("--cassette", type=Path, required=True, help="cassette file")
        command.add_argument(
            "--save-to", required=True, help="directory name under $TAU2_DATA_DIR/simulations"
        )
        add_run_settings(command)
    return parser


def _save(out_dir: Path, run: TaskRun, extra: dict) -> None:
    out_dir.mkdir(parents=True)
    (out_dir / "simulation.json").write_text(run.simulation_json, encoding="utf-8")
    outcome = RunOutcome.of(run.simulation, run.final_db_hash)
    write_outcome(out_dir, outcome, {"agent_version": run.agent_name, **extra})
    print(f"simulation: {run.simulation.simulation_id}")
    print(f"reward:     {outcome.reward} {outcome.reward_breakdown}")
    print(f"db hash:    {outcome.final_db_hash}")
    print(f"saved:      {out_dir}")


def _record(args: argparse.Namespace, settings: LiveRunSettings, out_dir: Path) -> int:
    if args.cassette.exists():
        print(f"{args.cassette} already exists; a cassette is never overwritten", file=sys.stderr)
        return 1
    factory = register_untraced_agent()
    with litellm_transport(RecordingTransport()) as transport:
        run = run_untraced_task(settings, factory)
    cassette = transport.cassette(settings_data(settings))
    sha256 = cassette.save(args.cassette)
    print(f"cassette:   {args.cassette} ({len(cassette.interactions)} responses)")
    print(f"sha256:     {sha256}")
    extra = {
        "mode": "record",
        "cassette": {
            "path": str(args.cassette),
            "sha256": sha256,
            "responses": len(cassette.interactions),
        },
    }
    _save(out_dir, run, extra)
    return 0


def _replay(args: argparse.Namespace, settings: LiveRunSettings, out_dir: Path) -> int:
    cassette = Cassette.load(args.cassette)
    if reason := settings_mismatch(settings, cassette):
        print(reason, file=sys.stderr)
        return 1
    sha256 = file_sha256(args.cassette)
    factory = register_untraced_agent()
    with replaying(cassette) as transport:
        try:
            run = run_untraced_task(settings, factory)
        except Exception as error:
            print(f"replay failed: {type(error).__name__}: {error}", file=sys.stderr)
            if transport.failure is not None:
                print(f"cassette: {transport.failure}", file=sys.stderr)
            return 1
    try:
        transport.check_complete()
    except CassetteMismatch as mismatch:
        print(f"cassette: {mismatch}", file=sys.stderr)
        return 1
    extra = {
        "mode": "replay",
        "tracing": "off",
        "cassette": {"path": str(args.cassette), "sha256": sha256, "served": transport.served},
    }
    _save(out_dir, run, extra)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = os.environ.get("TAU2_DATA_DIR")
    if not data_dir:
        print("TAU2_DATA_DIR is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    out_dir = Path(data_dir) / "simulations" / args.save_to
    if out_dir.exists():
        print(f"{out_dir} already exists; choose another --save-to", file=sys.stderr)
        return 1
    settings = run_settings(args)
    if args.command == "record":
        return _record(args, settings, out_dir)
    return _replay(args, settings, out_dir)


if __name__ == "__main__":
    sys.exit(main())
