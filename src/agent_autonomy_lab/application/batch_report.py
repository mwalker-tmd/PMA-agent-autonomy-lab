"""Turn a batch's rows into per-simulation records and the manifest's figures.

Every figure here is counted from the rows, with each non-scored status shown beside it
(docs/EVALUATION.md, "Rules for every metric"). No figure combines outcome and conduct.
"""

import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from agent_autonomy_lab.application.access_checks import authentication_compliance
from agent_autonomy_lab.application.batch_run import BatchOutcome, SimulationRow
from agent_autonomy_lab.application.confirmation_judge import CONFIRMATION_KEY
from agent_autonomy_lab.application.gate_checks import unauthorized_counts
from agent_autonomy_lab.application.task_success import benchmark_report
from agent_autonomy_lab.application.tool_correctness import (
    prohibited_actions,
    writes_match_reference,
)
from agent_autonomy_lab.domain.evaluation import EvaluationResult, ResultStatus, aggregate, roll_up
from agent_autonomy_lab.domain.evaluation.run_standing import RunClass
from agent_autonomy_lab.domain.events import argument_hash
from agent_autonomy_lab.domain.reference_writes import DEFECT_TASKS
from agent_autonomy_lab.domain.simulations import Message, Role
from agent_autonomy_lab.domain.tool_timeline import tool_timeline
from agent_autonomy_lab.ports.reference_writes import ReferenceWriteSource

_DROPPED_FROM_RECORD = ("scenario_id", "simulation_id")


def _messages(row: SimulationRow) -> tuple[Message, ...]:
    simulation = row.attempt.simulation
    return simulation.messages if simulation else ()


def _rollup_counts(statuses: Iterable[ResultStatus]) -> dict[str, Any]:
    counts = Counter(statuses)
    passed, failed = counts[ResultStatus.PASS], counts[ResultStatus.FAIL]
    return {
        "numerator": passed,
        "denominator": passed + failed,
        "not_applicable": counts[ResultStatus.NOT_APPLICABLE],
        "insufficient_evidence": counts[ResultStatus.INSUFFICIENT_EVIDENCE],
        "evaluator_error": counts[ResultStatus.EVALUATOR_ERROR],
        "invalid": counts[ResultStatus.INVALID],
    }


def _confirmation(results: Iterable[EvaluationResult]) -> dict[str, ResultStatus]:
    statuses: dict[str, list[ResultStatus]] = {}
    for result in results:
        if result.evaluation_key == CONFIRMATION_KEY:
            statuses.setdefault(result.simulation_id, []).append(result.status)
    return {simulation_id: roll_up(found) for simulation_id, found in statuses.items()}


ROLL_UPS: Mapping[str, Callable[[Iterable[EvaluationResult]], dict[str, ResultStatus]]] = {
    "authentication_compliance": authentication_compliance,
    "confirmation_compliance": _confirmation,
    "policy_compliance": prohibited_actions,
}


def _writes(row: SimulationRow, source: ReferenceWriteSource) -> dict[str, Any]:
    """How the writes compare with the reference, which is what the open question asks."""
    task_id = row.attempt.task_id
    reference = None if task_id in DEFECT_TASKS else source.reference_writes(task_id)
    return {
        "matches_reference": writes_match_reference(_messages(row), reference)
        if reference is not None and row.attempt.simulation
        else None
    }


def _hook_c(row: SimulationRow, judge_model: str) -> dict[str, Any]:
    """Hook C against what τ-bench's reward shows and what reached the provider (ADR-016)."""
    attempt = row.attempt
    reward = attempt.simulation.reward if attempt.simulation else None
    seen = (*attempt.callback_calls, *row.judge_callback_calls)
    sent = (*attempt.provider_calls, *row.judge_provider_calls)
    judge_seen = [call for call in seen if judge_model in str(call.get("model"))]
    costs = [call.get("cost") for call in judge_seen]
    return {
        "benchmark_judge": {
            "expected_calls": 1 if reward and reward.details.get("nl_assertions") else 0,
            "callback_calls": len(judge_seen),
            "provider_calls": sum(call.model == judge_model for call in sent),
            "prompt_tokens": sum(call.get("prompt") or 0 for call in judge_seen),
            "completion_tokens": sum(call.get("completion") or 0 for call in judge_seen),
            "litellm_cost_usd": None if None in costs else sum(costs),
        },
        "all_calls": {
            "callback_calls": len(seen),
            "provider_successes": sum(call.succeeded for call in sent),
            # By model, so a disagreement can be traced to the calls that caused it.
            "callback_by_model": dict(sorted(Counter(str(c.get("model")) for c in seen).items())),
            "provider_by_model": dict(
                sorted(Counter(call.model for call in sent if call.succeeded).items())
            ),
        },
    }


def _repeats_of_failed_calls(messages: Sequence[Message]) -> tuple[int, int]:
    """Calls repeating an earlier failed call with the same arguments, and the failed calls."""
    failed: set[tuple[str, str]] = set()
    repeats = failures = 0
    for record in tool_timeline(messages):
        unit = (record.call.name, argument_hash(record.call.arguments))
        repeats += unit in failed
        if record.result is not None and record.result.error:
            failed.add(unit)
            failures += 1
    return repeats, failures


def _usage(row: SimulationRow) -> dict[str, Any]:
    messages = _messages(row)
    tokens = {
        role.value: {
            "prompt": sum(m.tokens.prompt_tokens for m in messages if m.role is role and m.tokens),
            "completion": sum(
                m.tokens.completion_tokens for m in messages if m.role is role and m.tokens
            ),
        }
        for role in (Role.ASSISTANT, Role.USER)
    }
    costs = [m.cost for m in messages if m.is_model_output]
    repeats, failures = _repeats_of_failed_calls(messages)
    tool_results = [m for m in messages if m.role is Role.TOOL]
    return {
        "messages": len(messages),
        "duration_seconds": row.attempt.simulation.duration_seconds
        if row.attempt.simulation
        else None,
        "tokens": tokens,
        "agent_and_user_litellm_cost_usd": None if None in costs else sum(costs),
        "tool_results": len(tool_results),
        "failed_tool_results": sum(m.error for m in tool_results),
        "failed_calls": failures,
        "calls_repeating_a_failed_call": repeats,
        "model_calls_with_usage": sum(1 for m in messages if m.is_model_output and m.tokens),
    }


def _judge_cost(row: SimulationRow) -> float | None:
    """The lab judges' cost as LiteLLM reports it; ``None`` if any call's cost is unknown."""
    raw = row.output.raw_outputs if row.output else ()
    calls = [item.payload["call"] for item in raw if "call" in item.payload]
    costs = [call.get("cost_usd") for call in calls]
    return None if None in costs else sum(costs)


def simulation_record(
    row: SimulationRow, source: ReferenceWriteSource, benchmark_judge_model: str
) -> dict[str, Any]:
    """The JSON-ready record of one attempt, with no conversation in it."""
    attempt = row.attempt
    standing = row.standing
    trace = row.trace
    results = row.results
    return {
        "task_id": attempt.task_id,
        "trial": attempt.trial,
        "attempt": attempt.attempt,
        "trial_seed": attempt.trial_seed,
        "simulation_id": attempt.simulation_id,
        "thread_id": trace.thread_id if trace else None,
        "root_run_id": str(trace.root_run_id) if trace else None,
        "run_class": standing.run_class.value if standing else None,
        "standing_reason": standing.reason if standing else None,
        "stop_cause": standing.cause if standing else None,
        "error_message": attempt.unfinished.error_message if attempt.unfinished else None,
        "not_scored": row.not_scored,
        "trace_error": row.trace_error,
        "fingerprint_differences": attempt.fingerprint_differences,
        "benchmark": benchmark_report(attempt.simulation) if attempt.simulation else None,
        "writes": _writes(row, source),
        "roll_ups": {
            name: statuses[attempt.simulation_id].value
            for name, read in ROLL_UPS.items()
            if attempt.simulation_id in (statuses := read(results))
        },
        "usage": _usage(row),
        "lab_judge_litellm_cost_usd": _judge_cost(row),
        "hook_c": _hook_c(row, benchmark_judge_model),
        "spent_usd_after": round(row.spent_usd_after, 6),
        "results": [
            {k: v for k, v in result.to_dict().items() if k not in _DROPPED_FROM_RECORD}
            for result in results
        ],
    }


def _percentile(values: Sequence[float], share: float) -> float | None:
    """Nearest-rank percentile, which is always a value that was measured."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def _spread(values: Sequence[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3) if values else None,
        "p95": round(_percentile(values, 0.95), 3) if values else None,
    }


def _metric_table(results: Sequence[EvaluationResult]) -> dict[str, Any]:
    table = {}
    for key, metric in aggregate(results).items():
        scores = Counter(
            result.score
            for result in results
            if result.evaluation_key == key and result.judge_run and result.score is not None
        )
        table[key] = {
            "numerator": metric.numerator,
            "denominator": metric.denominator,
            "pass_rate": round(metric.pass_rate, 4) if metric.denominator else None,
            "not_applicable": metric.not_applicable,
            "insufficient_evidence": metric.insufficient_evidence,
            "evaluator_error": metric.evaluator_error,
            "invalid": metric.invalid,
            "invalid_reasons": dict(metric.invalid_reasons),
            "evaluator_versions": sorted(
                {r.evaluator_version for r in results if r.evaluation_key == key}
            ),
            **(
                {"score_distribution": {str(s): scores[s] for s in sorted(scores)}}
                if scores
                else {}
            ),
        }
    return table


def _task_success_components(rows: Sequence[SimulationRow]) -> dict[str, dict[str, int]]:
    """The reward's components apart, on the runs that still count (docs/EVALUATION.md).

    τ-bench gives ``NL_ASSERTION: 1.0`` to a task with no assertions, which checked nothing,
    so a component enters its denominator only when the run shows that check was made.
    """
    components: dict[str, dict[str, int]] = {}
    for row in rows:
        reward = row.attempt.simulation.reward if row.attempt.simulation else None
        if row.is_invalid or reward is None:
            continue
        checked = {
            "DB": reward.db_match is not None,
            "NL_ASSERTION": reward.details.get("nl_assertions"),
        }
        for name, score in reward.breakdown.items():
            if not checked.get(name, True):
                continue
            part = components.setdefault(name, {"numerator": 0, "denominator": 0})
            part["denominator"] += 1
            part["numerator"] += score == 1.0
    return components


def _runs(outcome: BatchOutcome) -> dict[str, Any]:
    rows = outcome.rows
    classes = Counter(row.standing.run_class if row.standing else None for row in rows)
    invalid = Counter(row.standing.reason for row in rows if row.is_invalid)
    causes = Counter(row.standing.cause or "not stated" for row in rows if row.is_invalid)
    planned = len(outcome.plan.units())
    return {
        "units_planned": planned,
        "attempts": len(rows),
        "completed": classes[RunClass.COMPLETED],
        "application_failure": classes[RunClass.APPLICATION_FAILURE],
        "invalid": {
            "count": classes[RunClass.INVALID],
            "reasons": dict(sorted(invalid.items())),
            "causes": dict(sorted(causes.items())),
        },
        "no_known_standing": classes[None],
        "not_scored": [
            {
                "task_id": row.attempt.task_id,
                "trial": row.attempt.trial,
                "attempt": row.attempt.attempt,
                "reason": row.not_scored,
            }
            for row in rows
            if row.not_scored
        ],
        "runner_retries": outcome.retries,
        "not_run": outcome.not_run,
        "stop_reason": outcome.stop_reason,
    }


def _operational(rows: Sequence[SimulationRow], ledger_usd: float) -> dict[str, Any]:
    completed = sum(
        1 for row in rows if row.standing and row.standing.run_class is RunClass.COMPLETED
    )
    usages = [_usage(row) for row in rows]
    turns = [
        m.generation_seconds
        for row in rows
        for m in _messages(row)
        if m.role is Role.ASSISTANT and m.generation_seconds
    ]
    durations = [u["duration_seconds"] for u in usages if u["messages"]]
    failed = sum(u["failed_calls"] for u in usages)
    return {
        "cost_per_completed_simulation_usd": round(ledger_usd / completed, 6)
        if completed
        else None,
        "latency_seconds": {"simulation": _spread(durations), "agent_turn": _spread(turns)},
        "tokens": {
            "prompt": sum(t["prompt"] for u in usages for t in u["tokens"].values()),
            "completion": sum(t["completion"] for u in usages for t in u["tokens"].values()),
            "model_calls_with_usage": sum(u["model_calls_with_usage"] for u in usages),
        },
        "failed_tools": {
            "numerator": sum(u["failed_tool_results"] for u in usages),
            "denominator": sum(u["tool_results"] for u in usages),
        },
        "tool_retries": {
            "numerator": sum(u["calls_repeating_a_failed_call"] for u in usages),
            "denominator": failed,
        },
        "turns": {
            "numerator": sum(u["messages"] for u in usages),
            "denominator": sum(1 for u in usages if u["messages"]),
        },
    }


def _hook_c_check(rows: Sequence[SimulationRow], judge_model: str) -> dict[str, Any]:
    """Compare hook C's counts with what each should equal, since it fails silently."""
    checks = [(row, _hook_c(row, judge_model)) for row in rows]
    judge = [check["benchmark_judge"] for _, check in checks]
    costs = [item["litellm_cost_usd"] for item in judge]
    mismatched = [
        {
            "simulation_id": row.attempt.simulation_id,
            "task_id": row.attempt.task_id,
            **check,
        }
        for row, check in checks
        if not (
            check["benchmark_judge"]["expected_calls"]
            == check["benchmark_judge"]["callback_calls"]
            == check["benchmark_judge"]["provider_calls"]
            and check["all_calls"]["callback_calls"] == check["all_calls"]["provider_successes"]
        )
    ]
    return {
        "benchmark_judge_model": judge_model,
        "expected_calls": sum(item["expected_calls"] for item in judge),
        "callback_calls": sum(item["callback_calls"] for item in judge),
        "provider_calls": sum(item["provider_calls"] for item in judge),
        "prompt_tokens": sum(item["prompt_tokens"] for item in judge),
        "completion_tokens": sum(item["completion_tokens"] for item in judge),
        "litellm_cost_usd": None if None in costs else round(sum(costs), 6),
        "all_calls_callback": sum(check["all_calls"]["callback_calls"] for _, check in checks),
        "all_calls_provider_successes": sum(
            check["all_calls"]["provider_successes"] for _, check in checks
        ),
        "mismatched_simulations": mismatched,
    }


def _divergent(rows: Sequence[SimulationRow], source: ReferenceWriteSource) -> dict[str, Any]:
    """Simulations that reached the right end state through other writes than the reference's."""
    compared = found = 0
    simulations = []
    for row in rows:
        simulation = row.attempt.simulation
        if row.is_invalid or simulation is None or simulation.reward is None:
            continue
        matches = _writes(row, source)["matches_reference"]
        if simulation.reward.db_match is not True or matches is None:
            continue
        compared += 1
        if matches is False:
            found += 1
            simulations.append(
                {
                    "task_id": row.attempt.task_id,
                    "trial": row.attempt.trial,
                    "simulation_id": row.attempt.simulation_id,
                    "reward": simulation.reward.reward,
                }
            )
    return {
        "correct_end_states_compared": compared,
        "reached_through_different_writes": found,
        "simulations": simulations,
    }


def _by_task(rows: Sequence[SimulationRow]) -> dict[str, list[dict[str, Any]]]:
    """Each task's trials side by side, which is where the spread across trials shows."""
    tasks: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        reward = row.attempt.simulation.reward if row.attempt.simulation else None
        policy = prohibited_actions(row.results).get(row.attempt.simulation_id)
        tasks.setdefault(row.attempt.task_id, []).append(
            {
                "trial": row.attempt.trial,
                "attempt": row.attempt.attempt,
                "run_class": row.standing.run_class.value if row.standing else None,
                "reward": reward.reward if reward else None,
                "db_match": reward.db_match if reward else None,
                "policy_compliance": policy.value if policy else None,
            }
        )
    return {task_id: tasks[task_id] for task_id in tasks}


def _litellm_estimate(rows: Sequence[SimulationRow], judge_model: str) -> dict[str, Any]:
    """LiteLLM's figures, reported because the contract names them; they are not the authority."""
    agent_user = [_usage(row)["agent_and_user_litellm_cost_usd"] for row in rows]
    judges = [_judge_cost(row) for row in rows]
    benchmark = [_hook_c(row, judge_model)["benchmark_judge"]["litellm_cost_usd"] for row in rows]

    def total(values: Sequence[float | None]) -> float | None:
        return None if None in values else round(sum(values), 6)

    return {
        "agent_and_user_usd": total(agent_user),
        "lab_judges_usd": total(judges),
        "benchmark_judge_usd": total(benchmark),
    }


def batch_figures(
    outcome: BatchOutcome, source: ReferenceWriteSource, benchmark_judge_model: str
) -> dict[str, Any]:
    """Every figure the manifest reports, counted from the rows."""
    rows = outcome.rows
    results = [result for row in rows for result in row.results]
    ledger = outcome.ledger
    return {
        "runs": _runs(outcome),
        "metrics": _metric_table(results),
        "simulation_roll_ups": {
            name: _rollup_counts(read(results).values()) for name, read in ROLL_UPS.items()
        },
        "task_success_components": _task_success_components(rows),
        "unauthorized_counts": [
            vars(count)
            for count in unauthorized_counts(
                (row.events, row.results) for row in rows if row.results
            )
        ],
        "tool_correctness_question": _divergent(rows, source),
        "by_task": _by_task(rows),
        "operational": _operational(rows, ledger.total_usd),
        "hook_c": _hook_c_check(rows, benchmark_judge_model),
        "cost": {
            "list_price_usd": round(ledger.total_usd, 6),
            "cap_usd": outcome.plan.cap_usd,
            "by_model": {
                model: {**row, "usd": round(row["usd"], 6)}
                for model, row in ledger.by_model().items()
            },
            "unpriced_calls": ledger.unpriced_calls,
            "litellm_estimate": _litellm_estimate(rows, benchmark_judge_model),
            "billed_usd": None,
        },
        "traces": {
            "sent": sum(1 for row in rows if row.trace),
            "not_sent": [
                {"simulation_id": row.attempt.simulation_id, "reason": row.trace_error}
                for row in rows
                if not row.trace
            ],
        },
    }
