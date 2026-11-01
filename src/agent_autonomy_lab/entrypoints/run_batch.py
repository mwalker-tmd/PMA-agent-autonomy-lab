"""Run a versioned task list through the traced lab agent and score every simulation.

The models are called, so a run costs money, and it stops at a hard cap at list price.
Run a one-task smoke first, on a task with an NL assertion so hook C sees τ-bench's judge,
then compare its spend with the budget the manifest reports:

    uv run --env-file .env --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.run_batch \\
        --only-task 4 --trials 1 --save-to m4-baseline-smoke \\
        --report-dir reports/m4-baseline/smoke

    uv run --env-file .env --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.run_batch \\
        --trials 3 --save-to m4-baseline --report-dir reports/m4-baseline

Every run goes through hook A (docs/BASELINE.md) and is labeled ``custom_lab_experiment``.
Run settings default to the Milestone 2 baseline's. The batch stops if an agent turn sends
anything upstream's ``llm_agent`` would not. Each attempt saves ``simulation.json``, its
trace events, its evaluators' raw outputs, and a cassette each for the run and the judges under
``$TAU2_DATA_DIR/simulations/<save-to>/``, outside the repository. ``--report-dir`` gets
``manifest.yaml`` and ``simulations.jsonl``, the latter written as each attempt finishes.
``--dry-run`` calls the models but sends no trace.

It exits 1 if the batch stopped early, an evaluator errored, a trace was not sent, or hook C's
counts disagree with what they should equal. A failed or low-scoring simulation does not
change the exit code.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import litellm
import yaml
from tau2.config import DEFAULT_LLM_NL_ASSERTIONS

from agent_autonomy_lab.adapters.langsmith import LangSmithTraceSink, langsmith_client
from agent_autonomy_lab.adapters.litellm import (
    CallCounter,
    RecordingTransport,
    effective_settings,
    litellm_transport,
    provider_calls,
)
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.adapters.tau2.live_run import (
    AGENT_NAME,
    LiveRunFailed,
    LiveRunSettings,
    register_traced_agent,
    run_traced_task,
)
from agent_autonomy_lab.adapters.tau2.reference_writes import GoldReplayReferenceWrites
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.application.access_checks import (
    AuthenticationOrderEvaluator,
    MessageFormEvaluator,
    UserAccessEvaluator,
)
from agent_autonomy_lab.application.batch_report import batch_figures, simulation_record
from agent_autonomy_lab.application.batch_run import BatchOutcome, SimulationRow, run_batch
from agent_autonomy_lab.application.confirmation_judge import ConfirmationEvaluator
from agent_autonomy_lab.application.gate_checks import evaluate_with_unauthorized_counts
from agent_autonomy_lab.application.rubric_judges import rubric_judges
from agent_autonomy_lab.application.task_success import TaskSuccessEvaluator
from agent_autonomy_lab.application.tool_correctness import (
    ExpectedWritesEvaluator,
    prohibited_action_evaluators,
)
from agent_autonomy_lab.application.write_eligibility import (
    OneReplayPerSimulation,
    write_eligibility_evaluators,
)
from agent_autonomy_lab.domain.agent_turns import UNFINISHED_PREFIX, UnfinishedSimulation
from agent_autonomy_lab.domain.batch import (
    BatchPlan,
    ModelPrice,
    SimulationAttempt,
    TaskList,
)
from agent_autonomy_lab.domain.evaluation import (
    JUDGE_MODEL,
    JUDGE_SETTINGS,
    EvaluationSubject,
    EvaluatorOutput,
)
from agent_autonomy_lab.domain.events import ExperimentClass, TrafficType, new_id
from agent_autonomy_lab.entrypoints.run_options import (
    BASELINE_ARGS,
    BASELINE_MODEL,
    settings_data,
)
from agent_autonomy_lab.entrypoints.settings import langsmith_project
from agent_autonomy_lab.ports.batch import ScoredSimulation
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink

DEFAULT_TASK_LIST = Path("datasets/m4-task-sets-v1.json")
DEFAULT_CAP_USD = 5.0

# Per 1M tokens: gpt-6-luna from ADR-014 and claude-haiku-5-5 from ADR-020. τ-bench's judge is
# the price in LiteLLM's map at the pin, the only source the repository has for it.
LIST_PRICES = {
    "gpt-6-luna": ModelPrice(0.10, 0.50),
    JUDGE_MODEL: ModelPrice(0.10, 0.50),
    DEFAULT_LLM_NL_ASSERTIONS: ModelPrice(0.75, 4.50),
}

# #109: about 60 simulations, with room for longer rubric prompts.
BUDGET_TOTAL_USD = (0.70, 1.10)
BUDGET_SIMULATIONS = 60


def read_task_list(path: Path, set_name: str, only: Sequence[str] = ()) -> TaskList:
    """Read one set of a versioned task-set file; ``only`` must name tasks in that set."""
    raw = path.read_bytes()
    data = json.loads(raw)
    tasks = [task["id"] for task in data[set_name]["tasks"]]
    if unknown := sorted(set(only) - set(tasks)):
        raise ValueError(f"not in {data['version']} {set_name}: {', '.join(unknown)}")
    return TaskList(
        version=data["version"],
        set_name=set_name,
        task_ids=tuple(only) if only else tuple(tasks),
        sha256=hashlib.sha256(raw).hexdigest(),
        tau_bench_commit=data["tau_bench_commit"],
    )


def _write_raw_outputs(path: Path, output: EvaluatorOutput) -> None:
    """What each ``raw_output_id`` names: a judge's reply, or why its call failed."""
    records = (
        {
            "output_id": str(raw.output_id),
            "evaluation_key": raw.evaluation_key,
            "evaluator_version": raw.evaluator_version,
            "simulation_id": raw.simulation_id,
            "payload": raw.payload,
        }
        for raw in output.raw_outputs
    )
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _lab_commit(start: Path | None = None) -> str:
    """The commit that ran, marked if its source had changes, since a manifest must name it."""
    git = ("git", "-C", str(start or Path(__file__).parent))
    commit = subprocess.run((*git, "rev-parse", "HEAD"), capture_output=True, text=True)
    # ``:/`` anchors the path at the repository's root, wherever the command runs from.
    changes = subprocess.run(
        (*git, "status", "--porcelain", "--", ":/src"), capture_output=True, text=True
    )
    if commit.returncode:
        return "unknown"
    return commit.stdout.strip() + ("+changes" if changes.stdout.strip() else "")


class LiveSession:
    """The real runner and scorer: one process, one registered agent, one saved directory."""

    def __init__(self, settings_for_task, out_dir: Path) -> None:
        self._settings_for_task = settings_for_task
        self._out_dir = out_dir
        self._factory = register_traced_agent()
        self._counter = CallCounter()
        litellm.callbacks = [self._counter]
        self._client = LiteLLMModelClient()
        state = OneReplayPerSimulation(ReplayedRetailState())
        self.references = GoldReplayReferenceWrites()
        self._evaluators = (
            AuthenticationOrderEvaluator(),
            UserAccessEvaluator(state),
            MessageFormEvaluator(),
            *write_eligibility_evaluators(state),
            *prohibited_action_evaluators(state, self.references),
            ExpectedWritesEvaluator(self.references),
            TaskSuccessEvaluator(),
            ConfirmationEvaluator(self._client),
            *rubric_judges(self._client),
        )
        self._directories: dict[str, Path] = {}

    def _observed(self, before: int, expected: int) -> tuple[Mapping[str, Any], ...]:
        self._counter.wait_for(before + expected)
        return tuple(self._counter.calls[before:])

    def run(self, task_id: str, trial: int, attempt: int) -> SimulationAttempt:
        settings: LiveRunSettings = self._settings_for_task(task_id)
        directory = self._out_dir / f"task-{task_id}-trial-{trial}-attempt-{attempt}"
        directory.mkdir(parents=True)
        recorder = RecordingTransport()
        before = len(self._counter.calls)
        shared: dict[str, Any]
        with litellm_transport(recorder):
            try:
                live = run_traced_task(settings, self._factory, trial=trial)
            except LiveRunFailed as failed:
                live, failure = None, failed.failure
                shared = {
                    "trial_seed": failure.trial_seed,
                    "reference": failure.reference,
                    "policy_version": failure.policy_version,
                    "agent_name": failure.agent_name,
                    "recording": failure.recording,
                    "unfinished": UnfinishedSimulation(
                        simulation_id=f"{UNFINISHED_PREFIX}{new_id()}",
                        task_id=task_id,
                        error_class=type(failure.cause).__name__,
                        error_message=str(failure.cause) or None,
                    ),
                }
            else:
                shared = {
                    "trial_seed": live.trial_seed,
                    "reference": live.reference,
                    "policy_version": live.policy_version,
                    "agent_name": live.agent_name,
                    "recording": live.recording,
                    "simulation": live.simulation,
                }
        calls = provider_calls(recorder.interactions)
        observed = self._observed(before, sum(call.succeeded for call in calls))
        recorder.cassette(settings_data(settings)).save(directory / "run.cassette.json")
        if live is not None:
            (directory / "simulation.json").write_text(live.simulation_json, encoding="utf-8")
        result = SimulationAttempt(
            task_id=task_id,
            trial=trial,
            attempt=attempt,
            provider_calls=calls,
            callback_calls=observed,
            effective_settings=effective_settings(recorder.interactions, DEFAULT_LLM_NL_ASSERTIONS),
            **shared,
        )
        (directory / "attempt.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "trial": trial,
                    "attempt": attempt,
                    "trial_seed": result.trial_seed,
                    "simulation_id": result.simulation_id,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._directories[result.simulation_id] = directory
        return result

    def save_trace_events(self, row: SimulationRow) -> None:
        """Keep the sent IDs for every attempt, scored or not, since traces expire."""
        directory = self._directories[row.attempt.simulation_id]
        lines = [json.dumps(event.to_dict(), ensure_ascii=False) for event in row.events]
        path = directory / "trace_events.jsonl"
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    def score(self, subject: EvaluationSubject) -> ScoredSimulation:
        directory = self._directories[subject.simulation_id]
        recorder = RecordingTransport()
        before = len(self._counter.calls)
        with litellm_transport(recorder):
            output = evaluate_with_unauthorized_counts(subject, self._evaluators)
        recorder.cassette({"run_batch_judges": True}).save(directory / "judges.cassette.json")
        _write_raw_outputs(directory / "raw_outputs.jsonl", output)
        calls = provider_calls(recorder.interactions)
        observed = self._observed(before, sum(call.succeeded for call in calls))
        return ScoredSimulation(output, calls, observed)


def _fingerprint(attempt: SimulationAttempt) -> dict[str, Any]:
    sent = attempt.reference
    return {
        "model_id": sent.model_id,
        "prompt_version": sent.prompt_version,
        "tool_schema_version": sent.tool_schema_version,
        "model_args_version": sent.model_args_version,
    }


def _distinct(values: Sequence[Any]) -> list[Any]:
    """Each distinct value once, in the order first seen."""
    seen: dict[str, Any] = {}
    for value in values:
        seen.setdefault(json.dumps(value, sort_keys=True, default=str), value)
    return list(seen.values())


def _effective(rows: Sequence[SimulationRow]) -> dict[str, Any]:
    roles: dict[str, list[Any]] = {}
    for row in rows:
        for role, setting in row.attempt.effective_settings.items():
            roles.setdefault(role, []).append(setting)
    judges = [
        result.judge_run.settings_sent
        for row in rows
        for result in row.results
        if result.judge_run and result.judge_run.settings_sent is not None
    ]
    return {
        **{role: _distinct(found) for role, found in roles.items()},
        "judges": _distinct(judges),
    }


def _budget(outcome: BatchOutcome) -> dict[str, Any]:
    """The run's spend against #109's budget, scaled to the budget's 60 simulations."""
    attempts = len(outcome.rows)
    spent = outcome.ledger.total_usd
    per_simulation = spent / attempts if attempts else None
    projected = per_simulation * BUDGET_SIMULATIONS if per_simulation is not None else None
    low, high = BUDGET_TOTAL_USD
    return {
        "expected_total_usd": [low, high],
        "expected_per_simulation_usd": [
            round(low / BUDGET_SIMULATIONS, 4),
            round(high / BUDGET_SIMULATIONS, 4),
        ],
        "list_price_per_attempt_usd": round(per_simulation, 6) if per_simulation else None,
        "projected_60_simulations_usd": round(projected, 4) if projected is not None else None,
        "within_expected_total": low <= projected <= high if projected is not None else None,
        "under_cap_when_projected": projected < outcome.plan.cap_usd if projected else None,
    }


def manifest(
    outcome: BatchOutcome,
    figures: Mapping[str, Any],
    experiment_id: str,
    started_at: datetime,
    finished_at: datetime,
    report_path: Path | None,
    project: str | None,
    command: str,
    seed: int,
) -> dict[str, Any]:
    """The reproducibility template (DEVELOPMENT.md, section 5), then the run's figures."""
    plan = outcome.plan
    rows = outcome.rows
    first = rows[0].attempt if rows else None
    runs = figures["runs"]
    return {
        "experiment_id": experiment_id,
        "started_at_utc": started_at.isoformat(timespec="seconds"),
        "finished_at_utc": finished_at.isoformat(timespec="seconds"),
        "traffic_type": TrafficType.SYNTHETIC.value,
        "experiment_class": ExperimentClass.CUSTOM_LAB_EXPERIMENT.value,
        "lab_commit": _lab_commit(),
        "tau_bench_commit": plan.task_list.tau_bench_commit,
        "tau_bench_task_split": "base",
        "selected_task_ids": list(plan.task_list.task_ids),
        "scenario_manifest_version": plan.task_list.version,
        "scenario_manifest_set": plan.task_list.set_name,
        "scenario_manifest_sha256": plan.task_list.sha256,
        "model_id": BASELINE_MODEL,
        "user_model_id": BASELINE_MODEL,
        "judge_model": {
            "lab_judges": JUDGE_MODEL,
            "tau_bench_nl_assertions": DEFAULT_LLM_NL_ASSERTIONS,
        },
        "declared_settings": {
            "agent": json.loads(BASELINE_ARGS),
            "user": json.loads(BASELINE_ARGS),
            "lab_judges": dict(JUDGE_SETTINGS),
        },
        "effective_settings": _effective(rows),
        "agent_version": first.agent_name if first else AGENT_NAME,
        "prompt_version": first.reference.prompt_version if first else None,
        "policy_version": first.policy_version if first else None,
        "upstream_fingerprint": _distinct([_fingerprint(row.attempt) for row in rows]),
        "fingerprint_mismatched_attempts": sum(
            1 for row in rows if row.attempt.fingerprint_differences
        ),
        "seed": seed,
        "trial_seeds": sorted({row.attempt.trial_seed for row in rows}),
        "trials_per_task": plan.trials,
        "concurrency": 1,
        "attempts": runs["attempts"],
        "completed": runs["completed"],
        "failed_to_run": runs["invalid"]["count"],
        "langsmith_project": project,
        "report_path": str(report_path) if report_path else None,
        "command": command,
        "litellm_version": metadata.version("litellm"),
        "notes": None,
        "budget": _budget(outcome),
        **figures,
    }


def _exit_code(outcome: BatchOutcome, figures: Mapping[str, Any]) -> int:
    errors = any(metric["evaluator_error"] for metric in figures["metrics"].values())
    trouble = (
        outcome.stop_reason
        or errors
        or figures["traces"]["not_sent"]
        or figures["hook_c"]["mismatched_simulations"]
    )
    return 1 if trouble else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task-list", type=Path, default=DEFAULT_TASK_LIST)
    # The held-out set is not run or inspected in Milestone 4 (docs/EVALUATION.md, "Task sets").
    parser.add_argument(
        "--set", default="baseline", choices=("baseline",), help="the set in the task list"
    )
    parser.add_argument("--only-task", action="append", default=[], help="run only this task")
    parser.add_argument("--trials", type=int, required=True, help="trials per task")
    parser.add_argument(
        "--cap-usd", type=float, default=DEFAULT_CAP_USD, help="hard cap at list price"
    )
    parser.add_argument(
        "--max-retries", type=int, default=1, help="retries of a trial whose run was INVALID"
    )
    parser.add_argument("--seed", type=int, default=42, help="run seed, as for tau2 run")
    parser.add_argument(
        "--save-to", required=True, help="directory name under $TAU2_DATA_DIR/simulations"
    )
    parser.add_argument(
        "--report-dir", type=Path, required=True, help="where the manifest and results go"
    )
    parser.add_argument("--dry-run", action="store_true", help="send no trace to LangSmith")
    return parser


def _sink(dry_run: bool, project: str) -> TraceSink:
    if dry_run:
        return InMemoryTraceSink()
    # Synchronous sends, so a run LangSmith rejects raises instead of only being logged.
    return LangSmithTraceSink(langsmith_client(auto_batch_tracing=False), project)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.dry_run and not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    data_dir = os.environ.get("TAU2_DATA_DIR")
    if not data_dir:
        print("TAU2_DATA_DIR is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    out_dir = Path(data_dir) / "simulations" / args.save_to
    results_path = args.report_dir / "simulations.jsonl"
    for taken in (out_dir, results_path):
        if taken.exists():
            print(f"{taken} already exists; a run is never overwritten", file=sys.stderr)
            return 1
    plan = BatchPlan(
        read_task_list(args.task_list, args.set, args.only_task),
        args.trials,
        args.cap_usd,
        args.max_retries,
    )
    project = langsmith_project()
    model_args = json.loads(BASELINE_ARGS)

    def settings_for_task(task_id: str) -> LiveRunSettings:
        return LiveRunSettings(
            task_id=task_id,
            agent_llm=BASELINE_MODEL,
            user_llm=BASELINE_MODEL,
            agent_llm_args=model_args,
            user_llm_args=model_args,
            seed=args.seed,
        )

    session = LiveSession(settings_for_task, out_dir)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    began = time.perf_counter()

    def write_row(row: SimulationRow) -> None:
        session.save_trace_events(row)
        record = simulation_record(row, session.references, DEFAULT_LLM_NL_ASSERTIONS)
        with results_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"task {row.attempt.task_id} trial {row.attempt.trial} "
            f"attempt {row.attempt.attempt}: {record['run_class']}, "
            f"spent ${row.spent_usd_after:.4f}, {time.perf_counter() - began:.0f}s",
            file=sys.stderr,
        )

    outcome = run_batch(
        plan,
        session,
        session,
        _sink(args.dry_run, project),
        BASELINE_MODEL,
        LIST_PRICES,
        write_row,
    )
    figures = batch_figures(outcome, session.references, DEFAULT_LLM_NL_ASSERTIONS)
    data = manifest(
        outcome,
        figures,
        args.save_to,
        started_at,
        datetime.now(UTC),
        results_path,
        None if args.dry_run else project,
        "python -m agent_autonomy_lab.entrypoints.run_batch "
        + " ".join(sys.argv[1:] if argv is None else argv),
        args.seed,
    )
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    (args.report_dir / "manifest.yaml").write_text(text, encoding="utf-8")
    print(json.dumps(data["budget"], indent=2))
    if outcome.stop_reason:
        print(f"stopped: {outcome.stop_reason}", file=sys.stderr)
    return _exit_code(outcome, figures)


if __name__ == "__main__":
    sys.exit(main())
