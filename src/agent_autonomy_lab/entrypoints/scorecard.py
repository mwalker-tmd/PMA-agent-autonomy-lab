"""Re-score the baseline with no model call, bundle its runs, and render the scorecard.

``rescore`` reads an extracted release bundle, or the local save directories, and reruns every
check and judge. Checks read the saved runs, and judges are served from the recordings, so
provider keys are placeholders and no request can leave. It writes what it found and exits 1
if a file's hash or any result differs from what was recorded:

    tar -xzf m4-baseline-bundle.tar.gz -C BUNDLE
    TAU2_DATA_DIR=../tau2-bench/data uv run python -m \\
        agent_autonomy_lab.entrypoints.scorecard rescore BUNDLE

``render`` rebuilds the scorecard's tables and the sample judgments from committed files, and
``render --check`` exits 1 if they are out of date. ``evidence`` shows what one simulation's
results cite. ``bundle`` writes the archive for the release and its committed manifest.
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import tarfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml

from agent_autonomy_lab.adapters.litellm import Cassette, file_sha256, replaying_by_request
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.adapters.tau2.reference_writes import GoldReplayReferenceWrites
from agent_autonomy_lab.application.adjudication import with_rescored_rubrics
from agent_autonomy_lab.application.confirmation_judge import ConfirmationEvaluator
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.gate_checks import evaluate_with_unauthorized_counts
from agent_autonomy_lab.application.rubric_judges import RUBRIC_V1, rubric_judges
from agent_autonomy_lab.application.scorecard import (
    checks_table,
    compare_results,
    contrast_table,
    evidence_coverage,
    evidence_table,
    judges_table,
    merge_comparisons,
    metrics_table,
    operational_table,
    outcome_conduct_table,
    replace_blocks,
    reproduction_table,
    sample_judgments,
)
from agent_autonomy_lab.domain.evaluation import EvaluationSubject
from agent_autonomy_lab.domain.events import TraceEvent
from agent_autonomy_lab.entrypoints.adjudicate import repeat_lines, sampled_subject
from agent_autonomy_lab.entrypoints.replay_state import saved_simulations
from agent_autonomy_lab.entrypoints.run_batch import scoring_evaluators

REPORTS = Path("reports/m4-baseline")
BASELINE_DIR = "m4-baseline"
RECORDINGS_DIR = "m4-adjudication"
ARCHIVE_NAME = "m4-baseline-bundle.tar.gz"
# Reward 1 with a part A FAIL, and reward 0 with a part A PASS.
CONTRASTS = (("76", 0), ("68", 1))
_REPEATS = {
    "v1": ("adjudication-repeats.jsonl", ""),
    "v2": ("adjudication-repeats-rubric-v2.jsonl", "rubric-v2"),
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _yaml_text(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _run_dir(save_dir: Path, row: dict) -> Path:
    return save_dir / f"task-{row['task_id']}-trial-{row['trial']}-attempt-{row['attempt']}"


def _saved_events(directory: Path) -> tuple[TraceEvent, ...]:
    return tuple(TraceEvent.from_dict(line) for line in _jsonl(directory / "trace_events.jsonl"))


def _subject(directory: Path, row: dict) -> EvaluationSubject:
    """The run as the batch scored it, citing the spans it sent."""
    _, (simulation,) = saved_simulations(str(directory / "simulation.json"))
    if simulation.simulation_id != row["simulation_id"]:
        raise ValueError(f"{directory.name} holds {simulation.simulation_id}")
    return EvaluationSubject(
        simulation_id=simulation.simulation_id,
        simulation=simulation,
        events=_saved_events(directory),
        scenario_id=row["task_id"],
    )


def _replay_baseline(save_dir: Path, rows: Sequence[dict]) -> dict[str, Any]:
    references = GoldReplayReferenceWrites()
    comparisons, coverage = [], []
    served = unmatched = unserved = 0
    unmatched_where = []
    for row in rows:
        directory = _run_dir(save_dir, row)
        subject = _subject(directory, row)
        with replaying_by_request(Cassette.load(directory / "judges.cassette.json")) as transport:
            evaluators = scoring_evaluators(LiteLLMModelClient(), references, RUBRIC_V1)
            output = evaluate_with_unauthorized_counts(subject, evaluators)
        where = f"task {row['task_id']} trial {row['trial']}"
        replayed = [result.to_dict() for result in output.results]
        comparisons.append(compare_results(row["results"], replayed, where))
        known = (str(event.event_id) for event in subject.events)
        coverage.append(evidence_coverage(row["results"], known))
        served, unserved = served + transport.served, unserved + transport.unserved
        unmatched += transport.unmatched
        if transport.unmatched:
            unmatched_where.append(where)
    return {
        **merge_comparisons(comparisons),
        "requests_served": served,
        "requests_with_no_recording": unmatched,
        "requests_with_no_recording_in": unmatched_where,
        "recordings_unserved": unserved,
        "evidence": _total(coverage),
    }


def _total(parts: Iterable[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for part in parts:
        for name, value in part.items():
            total[name] = total.get(name, 0) + value
    return total


def _replay_rubric_v2(save_dir: Path, recordings: Path, rows: Sequence[dict]) -> dict[str, Any]:
    """The re-score cited the trace rebuilt from each saved run, so it is checked against that."""
    by_id = {row["simulation_id"]: row for row in rows}
    comparisons, coverage = [], []
    cassette = Cassette.load(recordings / "rescore-rubric-v2.cassette.json")
    with replaying_by_request(cassette) as transport:
        judges = rubric_judges(LiteLLMModelClient())
        for line in _jsonl(REPORTS / "adjudication-rescore-rubric-v2.jsonl"):
            subject = sampled_subject(save_dir, by_id[line["simulation_id"]])
            output = evaluate_simulation(subject, judges)
            replayed = [result.to_dict() for result in output.results]
            where = f"v2 task {line['task_id']} trial {line['trial']}"
            comparisons.append(compare_results(line["results"], replayed, where))
            known = (str(event.event_id) for event in subject.events)
            coverage.append(evidence_coverage(line["results"], known))
    return {
        **merge_comparisons(comparisons),
        "requests_served": transport.served,
        "requests_with_no_recording": transport.unmatched,
        "recordings_unserved": transport.unserved,
        "evidence_in_rebuilt_trace": _total(coverage),
    }


def _replay_repeats(save_dir: Path, recordings: Path, rows: Sequence[dict]) -> dict[str, Any]:
    by_id = {row["simulation_id"]: row for row in rows}
    cases = yaml.safe_load((REPORTS / "adjudication-sample.yaml").read_text("utf-8"))["cases"]
    report = {}
    for name, (file_name, folder) in _REPEATS.items():
        recorded = _jsonl(REPORTS / file_name)
        replayed, unserved, unmatched = [], 0, 0
        for run in sorted({line["run"] for line in recorded}):
            path = recordings / folder / f"run-{run}.cassette.json"
            with replaying_by_request(Cassette.load(path)) as transport:
                client = LiteLLMModelClient()
                judges = (
                    (ConfirmationEvaluator(client), *rubric_judges(client, RUBRIC_V1))
                    if name == "v1"
                    else rubric_judges(client)
                )
                for case in cases:
                    subject = sampled_subject(save_dir, by_id[case["simulation_id"]])
                    output = evaluate_simulation(subject, judges)
                    replayed.extend(repeat_lines(case, run, output))
            unserved += transport.unserved
            unmatched += transport.unmatched
        same = sum(a == b for a, b in zip(recorded, replayed, strict=False))
        report[name] = {
            "compared": len(recorded),
            "replayed": len(replayed),
            "identical": same,
            "requests_with_no_recording": unmatched,
            "recordings_unserved": unserved,
        }
    return report


def _bundle_files(root: Path) -> list[Path]:
    """The files in the bundle, in a fixed order, with no OS clutter."""
    found = [
        path
        for name in (BASELINE_DIR, RECORDINGS_DIR)
        for path in (root / name).rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    ]
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def _check_hashes(root: Path, manifest: dict) -> dict[str, Any]:
    recorded = {item["path"]: item["sha256"] for item in manifest["files"]}
    present = {path.relative_to(root).as_posix(): path for path in _bundle_files(root)}
    missing = sorted(set(recorded) - set(present))
    extra = sorted(set(present) - set(recorded))
    changed = sorted(
        name
        for name in set(recorded) & set(present)
        if file_sha256(present[name]) != recorded[name]
    )
    return {"files": len(recorded), "missing": missing, "extra": extra, "changed": changed}


def _passed(check: dict) -> bool:
    files = check["bundle_files"]
    if files["missing"] or files["extra"] or files["changed"]:
        return False
    baseline, rubric = check["baseline"], check["rubric_v2"]
    return not (
        baseline["differences"]
        or rubric["differences"]
        or baseline["recordings_unserved"]
        or rubric["recordings_unserved"]
        or rubric["requests_with_no_recording"]
        or any(
            part["identical"] != part["compared"]
            or part["replayed"] != part["compared"]
            or part["requests_with_no_recording"]
            or part["recordings_unserved"]
            for part in check["repeats"].values()
        )
    )


def rescore(args: argparse.Namespace) -> int:
    if not os.environ.get("TAU2_DATA_DIR"):
        print(
            "TAU2_DATA_DIR is not set: point it at the pinned τ-bench clone's data/.",
            file=sys.stderr,
        )
        return 1
    save_dir, recordings = args.bundle / BASELINE_DIR, args.bundle / RECORDINGS_DIR
    manifest = yaml.safe_load((REPORTS / "bundle-manifest.yaml").read_text("utf-8"))
    files = _check_hashes(args.bundle, manifest)
    rows = _jsonl(REPORTS / "simulations.jsonl")
    check = {
        "bundle_files": files,
        "baseline": _replay_baseline(save_dir, rows),
        "rubric_v2": _replay_rubric_v2(save_dir, recordings, rows),
        "repeats": _replay_repeats(save_dir, recordings, rows),
    }
    check["reproduced"] = _passed(check)
    args.out.write_text(_yaml_text(check), encoding="utf-8")
    print(_yaml_text(check))
    return 0 if check["reproduced"] else 1


def bundle(args: argparse.Namespace) -> int:
    """A byte-stable archive: sorted paths, with no time or owner in it, so its hash repeats."""
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; a bundle is never overwritten")
    files = _bundle_files(args.source)

    def clean(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.mtime, info.uid, info.gid = 0, 0, 0
        info.uname, info.gname = "", ""
        info.mode = 0o644
        return info

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as raw, gzip.GzipFile("", "wb", fileobj=raw, mtime=0) as packed:
        with tarfile.open(fileobj=packed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for path in files:
                archive.add(path, arcname=path.relative_to(args.source).as_posix(), filter=clean)
    record = {
        "issue": 111,
        "attach_to_release": "v0.4.0-m4",
        "archive": ARCHIVE_NAME,
        "archive_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "archive_bytes": args.out.stat().st_size,
        "files": [
            {"path": path.relative_to(args.source).as_posix(), "sha256": file_sha256(path)}
            for path in files
        ],
    }
    (REPORTS / "bundle-manifest.yaml").write_text(_yaml_text(record), encoding="utf-8")
    print(f"{len(files)} files, {record['archive_bytes']} bytes, {record['archive_sha256']}")
    return 0


def _rendered() -> dict[Path, str]:
    rows = _jsonl(REPORTS / "simulations.jsonl")
    by_id = {row["simulation_id"]: row for row in rows}
    rescored = {
        line["simulation_id"]: with_rescored_rubrics(by_id[line["simulation_id"]], line["results"])
        for line in _jsonl(REPORTS / "adjudication-rescore-rubric-v2.jsonl")
    }
    manifest = yaml.safe_load((REPORTS / "manifest.yaml").read_text("utf-8"))
    check = yaml.safe_load((REPORTS / "rescore-check.yaml").read_text("utf-8"))
    cases = yaml.safe_load((REPORTS / "adjudication-sample.yaml").read_text("utf-8"))["cases"]
    labels = yaml.safe_load((REPORTS / "adjudication-labels.yaml").read_text("utf-8"))["labels"]
    card = REPORTS / "scorecard.md"
    samples = REPORTS / "sample-judgments.md"
    return {
        card: replace_blocks(
            card.read_text(encoding="utf-8"),
            {
                "outcome-conduct": outcome_conduct_table(rows),
                "metrics": metrics_table(rows, manifest),
                "judges": judges_table(rows, list(rescored.values())),
                "contrasts": contrast_table(rows, CONTRASTS),
                "checks": checks_table(rows),
                "operational": operational_table(manifest),
                "evidence": evidence_table(check),
                "reproduction": reproduction_table(check),
            },
        ),
        samples: (
            "# Sample judgments (M4-13, #111)\n\n"
            "Generated by `scorecard render`. All 24 adjudicated simulations of the "
            "[baseline scorecard](scorecard.md): the maintainer's blind label beside each "
            "judge's verdict and reason. The sample was chosen for its hard cases, so these "
            "are not representative of the baseline. See the "
            "[adjudication report](adjudication.md).\n\n"
            + sample_judgments(cases, labels, by_id, rescored)
            + "\n"
        ),
    }


def render(args: argparse.Namespace) -> int:
    outdated = []
    for path, text in _rendered().items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            outdated.append(str(path))
            if not args.check:
                path.write_text(text, encoding="utf-8")
    if args.check and outdated:
        print(f"out of date: {', '.join(outdated)}", file=sys.stderr)
        return 1
    return 0


def evidence(args: argparse.Namespace) -> int:
    """One simulation's results, each with the saved spans its evidence IDs name."""
    rows = _jsonl(REPORTS / "simulations.jsonl")
    row = next(r for r in rows if r["task_id"] == args.task and r["trial"] == args.trial)
    directory = _run_dir(args.bundle / BASELINE_DIR, row)
    events = {str(event.event_id): event for event in _saved_events(directory)}
    results = [
        {
            **{name: result[name] for name in ("evaluation_key", "status", "score", "reason")},
            "evidence": [
                {
                    "event_id": event_id,
                    "event_type": events[event_id].event_type.value,
                    "turn_index": events[event_id].turn_index,
                    "tool_name": events[event_id].tool_name,
                    "run_id": str(events[event_id].run_id),
                }
                for event_id in result["evidence_ids"]
            ],
        }
        for result in row["results"]
        if not args.key or result["evaluation_key"] == args.key
    ]
    print(
        _yaml_text(
            {"thread_id": row["thread_id"], "root_run_id": row["root_run_id"], "results": results}
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    again = commands.add_parser("rescore", help="reproduce the results with no model call")
    again.add_argument("bundle", type=Path, help="holds m4-baseline/ and m4-adjudication/")
    again.add_argument("--out", type=Path, default=REPORTS / "rescore-check.yaml")
    again.set_defaults(run=rescore)
    pack = commands.add_parser("bundle", help="write the release archive and its manifest")
    pack.add_argument("source", type=Path, help="holds m4-baseline/ and m4-adjudication/")
    pack.add_argument("--out", type=Path, required=True, help=f"the path of {ARCHIVE_NAME}")
    pack.set_defaults(run=bundle)
    show = commands.add_parser("render", help="rebuild the scorecard's tables")
    show.add_argument("--check", action="store_true", help="exit 1 if they are out of date")
    show.set_defaults(run=render)
    cited = commands.add_parser("evidence", help="show what one simulation's results cite")
    cited.add_argument("bundle", type=Path, help="holds m4-baseline/")
    cited.add_argument("--task", required=True)
    cited.add_argument("--trial", type=int, required=True)
    cited.add_argument("--key", help="only this evaluation key")
    cited.set_defaults(run=evidence)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
