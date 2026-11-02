"""Prepare the adjudication sample and the blind worksheet (#110).

``sample`` picks the cases and writes them with their strata, and the worksheet page goes
to ``--worksheet``. No model call is made:

    uv run python -m agent_autonomy_lab.entrypoints.adjudicate sample \\
        ../tau2-bench/data/simulations/m4-baseline \\
        --sample sample.yaml --worksheet worksheet.html

Keep the sample file away from the reviewer until the labels are in, because its strata say
what the checks found.

After the labels are committed, ``repeat`` runs every judge three more times on the sample
(live; about a quarter of a cent per simulation per run), and ``compare`` reads the labels,
the baseline, and the repeats, with no model call, and writes the counts the report quotes:

    uv run --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.adjudicate repeat \\
        ../tau2-bench/data/simulations/m4-baseline \\
        --recordings ../tau2-bench/data/simulations/m4-adjudication
    uv run python -m agent_autonomy_lab.entrypoints.adjudicate compare

A rubric fix is a new prompt version. ``rescore`` runs the three current rubric judges once on
every baseline simulation from its saved run, into its own file, and leaves the baseline's
report as it was. ``repeat --rubrics-only --out FILE`` repeats them on the sample, and
``compare --rescored FILE --rescored-repeats FILE`` reports before and after.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import yaml

from agent_autonomy_lab.adapters.litellm import RecordingTransport, litellm_transport
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.application.adjudication import (
    JUDGE_KEYS,
    RUBRIC_LABELS,
    SAMPLE_SEED,
    SAMPLE_SIZE,
    compare_labels,
    executed_a_write,
    judge_units,
    metric_counts,
    select_sample,
    self_agreement,
    with_rescored_rubrics,
    worksheet_case,
    worksheet_data,
)
from agent_autonomy_lab.application.confirmation_judge import ConfirmationEvaluator
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.rubric_judges import RUBRIC_V1, rubric_judges
from agent_autonomy_lab.domain.evaluation import EvaluationSubject
from agent_autonomy_lab.domain.simulations import Message
from agent_autonomy_lab.entrypoints.check_access import saved_subjects
from agent_autonomy_lab.entrypoints.judge_rubrics import CallUsage
from agent_autonomy_lab.entrypoints.replay_state import saved_simulations

REPORT = Path("reports/m4-baseline/simulations.jsonl")
TASK_SETS = Path("datasets/m4-task-sets-v1.json")
SAMPLE = Path("reports/m4-baseline/adjudication-sample.yaml")
LABELS = Path("reports/m4-baseline/adjudication-labels.yaml")
REPEATS = Path("reports/m4-baseline/adjudication-repeats.jsonl")
SUMMARY = Path("reports/m4-baseline/adjudication-summary.yaml")
RESCORED = Path("reports/m4-baseline/adjudication-rescore-rubric-v2.jsonl")
RESCORED_REPEATS = Path("reports/m4-baseline/adjudication-repeats-rubric-v2.jsonl")
RUNS = 3


def saved_messages(save_dir: Path, row: dict) -> tuple[Message, ...]:
    """The saved conversation of one row of the batch report."""
    name = f"task-{row['task_id']}-trial-{row['trial']}-attempt-{row['attempt']}"
    _, simulations = saved_simulations(str(save_dir / name / "simulation.json"))
    (simulation,) = simulations
    return simulation.messages


def sampled_subject(save_dir: Path, row: dict) -> EvaluationSubject:
    """The saved run of one report row, refused if it is not that row's simulation."""
    name = f"task-{row['task_id']}-trial-{row['trial']}-attempt-{row['attempt']}"
    ((_, subject),) = saved_subjects([str(save_dir / name / "simulation.json")])
    if subject.simulation_id != row["simulation_id"]:
        raise ValueError(f"{name} holds {subject.simulation_id}, not {row['simulation_id']}")
    return subject


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


def _load_rows(report: Path) -> dict[str, dict]:
    rows = (json.loads(line) for line in report.read_text(encoding="utf-8").splitlines())
    return {row["simulation_id"]: row for row in rows}


def repeat(args: argparse.Namespace) -> int:
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; remove it to run the repeats again")
    args.recordings.mkdir(parents=True, exist_ok=True)
    cases = yaml.safe_load(args.sample.read_text(encoding="utf-8"))["cases"]
    report = _load_rows(args.report)
    usage = CallUsage()
    hashes = {}
    lines = []
    for run in range(RUNS):
        recorder = RecordingTransport()
        client = LiteLLMModelClient()
        judges = (
            rubric_judges(client)
            if args.rubrics_only
            else (ConfirmationEvaluator(client), *rubric_judges(client, RUBRIC_V1))
        )
        with litellm_transport(recorder):
            for case in cases:
                subject = sampled_subject(args.save_dir, report[case["simulation_id"]])
                output = evaluate_simulation(subject, judges)
                usage.add(
                    raw.payload["call"] for raw in output.raw_outputs if "call" in raw.payload
                )
                rows = [result.to_dict() for result in output.results]
                for (key, unit), result in judge_units(rows).items():
                    lines.append(
                        {
                            "case_id": case["case_id"],
                            "simulation_id": case["simulation_id"],
                            "run": run,
                            "evaluation_key": key,
                            "unit": unit,
                            "status": result["status"],
                            "score": result.get("score"),
                            "reason": result.get("reason"),
                            "evaluator_version": result["evaluator_version"],
                        }
                    )
        recording = args.recordings / f"run-{run}.cassette.json"
        hashes[recording.name] = recorder.cassette({"adjudication_repeat": run}).save(recording)
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    args.out.write_text(text, encoding="utf-8")
    record = {"runs": RUNS, **usage.as_dict(), "recordings_sha256": hashes}
    print(json.dumps(record, indent=2))
    return 0


def rescore(args: argparse.Namespace) -> int:
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; remove it to re-score")
    if args.recording.exists():
        raise SystemExit(f"{args.recording} exists; remove it to re-score")
    args.recording.parent.mkdir(parents=True, exist_ok=True)
    recorder = RecordingTransport()
    judges = rubric_judges(LiteLLMModelClient())
    usage = CallUsage()
    lines = []
    with litellm_transport(recorder):
        for row in _load_rows(args.report).values():
            output = evaluate_simulation(sampled_subject(args.save_dir, row), judges)
            usage.add(raw.payload["call"] for raw in output.raw_outputs if "call" in raw.payload)
            lines.append(
                {
                    "simulation_id": row["simulation_id"],
                    "task_id": row["task_id"],
                    "trial": row["trial"],
                    "results": [result.to_dict() for result in output.results],
                }
            )
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    args.out.write_text(text, encoding="utf-8")
    sha = recorder.cassette({"adjudication_rescore": True}).save(args.recording)
    print(json.dumps({**usage.as_dict(), "recording_sha256": sha}, indent=2))
    return 0


def _rescored_section(args: argparse.Namespace, rows, labels, cases) -> dict:
    lines = [json.loads(line) for line in args.rescored.read_text("utf-8").splitlines()]
    after = {
        line["simulation_id"]: with_rescored_rubrics(rows[line["simulation_id"]], line["results"])
        for line in lines
    }
    repeats = [json.loads(line) for line in args.rescored_repeats.read_text("utf-8").splitlines()]
    return {
        "against_labels": compare_labels(labels, cases, after),
        "self_agreement": self_agreement(repeats, after),
        "all_60_before": {key: metric_counts(list(rows.values()), key) for key in RUBRIC_LABELS},
        "all_60_after": {key: metric_counts(list(after.values()), key) for key in RUBRIC_LABELS},
    }


def compare(args: argparse.Namespace) -> int:
    rows = _load_rows(args.report)
    cases = yaml.safe_load(args.sample.read_text(encoding="utf-8"))["cases"]
    labels = yaml.safe_load(args.labels.read_text(encoding="utf-8"))["labels"]
    repeats = [json.loads(line) for line in args.repeats.read_text("utf-8").splitlines()]
    by_id = {label["case_id"]: label for label in labels}
    summary = {
        "against_labels": compare_labels(labels, cases, rows),
        "self_agreement": self_agreement(repeats, rows),
        "cases": [
            {
                "case": case["case_id"],
                "task": case["task_id"],
                "trial": case["trial"],
                "strata": case["strata"],
                "reward": rows[case["simulation_id"]]["benchmark"]["reward"],
                "part_a": rows[case["simulation_id"]]["roll_ups"]["policy_compliance"],
                "handled_correctly": by_id[case["case_id"]]["handled_correctly"],
                "note": by_id[case["case_id"]]["note"],
            }
            for case in cases
        ],
    }
    if args.rescored:
        summary["after_rubric_fixes"] = _rescored_section(args, rows, labels, cases)
    text = yaml.safe_dump(summary, sort_keys=False, allow_unicode=True, width=100)
    args.out.write_text(text, encoding="utf-8")
    for key in JUDGE_KEYS:
        entry = summary["against_labels"]["judges"][key]
        print(key, {k: len(v) if isinstance(v, list) else v for k, v in entry.items()})
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
    again = commands.add_parser("repeat", help="run each judge three more times on the sample")
    again.add_argument("save_dir", type=Path, help="the baseline's save directory")
    again.add_argument("--recordings", type=Path, required=True)
    again.add_argument("--sample", type=Path, default=SAMPLE)
    again.add_argument("--out", type=Path, default=REPEATS)
    again.add_argument("--report", type=Path, default=REPORT)
    again.add_argument("--rubrics-only", action="store_true", help="the current rubric judges")
    again.set_defaults(run=repeat)
    fresh = commands.add_parser("rescore", help="re-score all simulations with changed rubrics")
    fresh.add_argument("save_dir", type=Path, help="the baseline's save directory")
    fresh.add_argument("--recording", type=Path, required=True)
    fresh.add_argument("--report", type=Path, default=REPORT)
    fresh.add_argument("--out", type=Path, default=RESCORED)
    fresh.set_defaults(run=rescore)
    versus = commands.add_parser("compare", help="compare the judges with the labels")
    versus.add_argument("--sample", type=Path, default=SAMPLE)
    versus.add_argument("--labels", type=Path, default=LABELS)
    versus.add_argument("--repeats", type=Path, default=REPEATS)
    versus.add_argument("--report", type=Path, default=REPORT)
    versus.add_argument("--out", type=Path, default=SUMMARY)
    versus.add_argument("--rescored", type=Path)
    versus.add_argument("--rescored-repeats", type=Path, default=RESCORED_REPEATS)
    versus.set_defaults(run=compare)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
