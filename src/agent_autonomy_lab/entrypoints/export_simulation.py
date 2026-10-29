"""Export a saved τ-bench run to LangSmith, one thread per simulation (hook D).

Run with:

    uv run --env-file .env python -m agent_autonomy_lab.entrypoints.export_simulation \\
        m2-baseline-2026-10-27-r3 --source-tz America/Denver \\
        --experiment-class official_upstream_baseline

``RUN`` is a ``--save-to`` name under ``$TAU2_DATA_DIR/simulations``, or a
path to a ``results.json`` or its directory. No model is called. Exporting the
same run again with the same ``--source-tz`` reuses its threads, runs, and
feedback; a different zone shifts the timestamps the run IDs are built from.
``--dry-run`` builds and checks the traces without sending them.
"""

import argparse
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langsmith import Client

from agent_autonomy_lab.adapters.langsmith import LangSmithTraceSink
from agent_autonomy_lab.adapters.tau2 import read_results, results_path
from agent_autonomy_lab.application.export_simulation import export_run
from agent_autonomy_lab.domain.events import ExperimentClass
from agent_autonomy_lab.entrypoints.settings import langsmith_project
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise argparse.ArgumentTypeError(f"unknown IANA time zone {name!r}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", metavar="RUN", help="--save-to name, results.json, or its directory")
    parser.add_argument(
        "--source-tz",
        type=_zone,
        required=True,
        help="IANA zone of the machine that ran the simulations, e.g. America/Denver",
    )
    parser.add_argument(
        "--experiment-class",
        type=ExperimentClass,
        choices=list(ExperimentClass),
        required=True,
        help="official_upstream_baseline for unmodified upstream runs",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="build and check the traces; send nothing"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.dry_run and not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    try:
        path = results_path(args.run)
    except FileNotFoundError as error:
        print(f"no saved run found: {error}", file=sys.stderr)
        return 1
    run = read_results(path, args.source_tz)
    project = langsmith_project()
    sink: TraceSink
    if args.dry_run:
        sink = InMemoryTraceSink()
    else:
        # Synchronous sends, so a run LangSmith rejects raises instead of only being logged.
        sink = LangSmithTraceSink(Client(auto_batch_tracing=False), project)
    exported = export_run(run, sink, args.experiment_class)
    if isinstance(sink, InMemoryTraceSink) and (errors := sink.link_errors()):
        print("\n".join(errors), file=sys.stderr)
        return 1

    print(f"results:  {path}")
    print(f"project:  {project}{' (dry run, nothing sent)' if args.dry_run else ''}")
    print(f"{'task':<5} {'simulation_id':<37} {'thread_id':<37} spans  feedback")
    for item in exported:
        print(
            f"{item.task_id:<5} {item.simulation_id:<37} {item.thread_id:<37} "
            f"{item.spans:>5}  {item.feedback:>8}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
