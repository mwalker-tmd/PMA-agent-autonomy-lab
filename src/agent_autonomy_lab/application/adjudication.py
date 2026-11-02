"""Choose the baseline's adjudication sample, and build what the maintainer labels blind.

Selection (#110, decided with the maintainer): every critical-risk simulation, every
simulation where outcome and conduct disagree, then filler from the policy-sensitive and
successful groups up to the sample size. Selection reads only roll-ups, the τ-bench
reward, expected writes, and the task set. No rubric score reaches it, and the confirmation
judge reaches it only through the checks that define critical risk.

"Critical risk" is a working definition for #110, not policy: a non-``PASS`` in a class
docs/AUTONOMY.md names as disqualifying, or an executed write that still failed expected
writes.

The worksheet holds what a judge sees, by ``judge_view``, plus the write list. It holds no
task, trial, simulation ID, reward, result, or stratum.
"""

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_autonomy_lab.application.judge_calls import judge_prompt
from agent_autonomy_lab.application.rubric_judges import judge_view
from agent_autonomy_lab.domain.simulations import Message, Role
from agent_autonomy_lab.domain.tool_timeline import WRITE_TOOLS, tool_timeline

SAMPLE_SIZE = 24
SAMPLE_SEED = 110
POLICY_FILLER = 4
REQUIRED_POLICY_TASKS = ("10", "50")
CRITICAL_CLASSES = frozenset(
    {
        "authentication_order",
        "user_access",
        "required_customer_confirmation",
        "unauthorized_attempts",
        "unauthorized_executions",
    }
)
JUDGE_PROMPTS = {
    "clarity": "clarity-v1",
    "efficiency": "efficiency-v1",
    "escalation": "escalation-v1",
    "confirmation": "confirmation-v1",
}
_NOT_CLEAN = frozenset({"FAIL", "INSUFFICIENT_EVIDENCE", "EVALUATOR_ERROR"})
_POLICY_RISKS = frozenset({"must_refuse", "escalation"})
_REPLY_FORMAT = "Reply with one JSON object"


@dataclass(frozen=True)
class SampleCase:
    """One simulation in the sample, with every group it belongs to."""

    case_id: str
    task_id: str
    trial: int
    simulation_id: str
    strata: tuple[str, ...]


def executed_a_write(messages: Sequence[Message]) -> bool:
    """Whether the record establishes that at least one write ran."""
    return any(
        record.call.name in WRITE_TOOLS and record.succeeded for record in tool_timeline(messages)
    )


def _is_critical(row: Mapping[str, Any], wrote: bool) -> bool:
    statuses = {(result["evaluation_key"], result["status"]) for result in row["results"]}
    named = any(key in CRITICAL_CLASSES and status in _NOT_CLEAN for key, status in statuses)
    return named or (wrote and ("expected_writes", "FAIL") in statuses)


def _disagrees(row: Mapping[str, Any]) -> bool:
    reward = row["benchmark"]["reward"]
    part_a = row["roll_ups"]["policy_compliance"]
    return (reward == 1.0 and part_a == "FAIL") or (reward == 0.0 and part_a == "PASS")


def _is_policy_sensitive(task: Mapping[str, Any]) -> bool:
    return task["category"] == "policy_sensitive" or bool(_POLICY_RISKS & set(task["risk_cases"]))


def _strata(row: Mapping[str, Any], task: Mapping[str, Any], wrote: bool) -> tuple[str, ...]:
    groups = []
    if _is_critical(row, wrote):
        groups.append("critical_risk")
    if _disagrees(row):
        groups.append("outcome_conduct_disagreement")
    groups.append("failed" if row["benchmark"]["reward"] == 0.0 else "successful")
    if _is_policy_sensitive(task):
        groups.append("policy_sensitive")
    return tuple(groups)


def _one_per_task(
    rows: Sequence[Mapping[str, Any]], rng: random.Random, count: int
) -> list[Mapping[str, Any]]:
    """``count`` rows from different tasks, each task's trial drawn by ``rng``."""
    by_task: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_task.setdefault(row["task_id"], []).append(row)
    tasks = rng.sample(sorted(by_task), count)
    return [rng.choice(sorted(by_task[task], key=lambda r: r["trial"])) for task in tasks]


def select_sample(
    rows: Sequence[Mapping[str, Any]],
    tasks: Mapping[str, Mapping[str, Any]],
    wrote: Mapping[str, bool],
    *,
    size: int = SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
) -> tuple[SampleCase, ...]:
    """The sample in a shuffled order, with case IDs that say nothing about a case.

    ``wrote`` maps a simulation ID to whether it executed a write.
    """
    rng = random.Random(seed)
    strata = {
        row["simulation_id"]: _strata(row, tasks[row["task_id"]], wrote[row["simulation_id"]])
        for row in rows
    }
    required = [
        row
        for row in rows
        if {"critical_risk", "outcome_conduct_disagreement"} & set(strata[row["simulation_id"]])
    ]
    taken = {row["simulation_id"] for row in required}
    clean = [
        row
        for row in rows
        if row["simulation_id"] not in taken
        and row["benchmark"]["reward"] == 1.0
        and row["roll_ups"]["policy_compliance"] == "PASS"
    ]
    policy = [r for r in clean if "policy_sensitive" in strata[r["simulation_id"]]]
    successful = [r for r in clean if "policy_sensitive" not in strata[r["simulation_id"]]]

    named = [
        rng.choice([r for r in policy if r["task_id"] == task]) for task in REQUIRED_POLICY_TASKS
    ]
    other_policy = [r for r in policy if r["task_id"] not in REQUIRED_POLICY_TASKS]
    picked_policy = named + _one_per_task(other_policy, rng, POLICY_FILLER - len(named))
    remaining = size - len(required) - len(picked_policy)
    if remaining < 0:
        raise ValueError(f"{len(required)} required cases do not fit a sample of {size}")
    chosen = [*required, *picked_policy, *_one_per_task(successful, rng, remaining)]

    rng.shuffle(chosen)
    return tuple(
        SampleCase(
            case_id=f"C{number:02d}",
            task_id=row["task_id"],
            trial=row["trial"],
            simulation_id=row["simulation_id"],
            strata=strata[row["simulation_id"]],
        )
        for number, row in enumerate(chosen, start=1)
    )


def worksheet_case(case_id: str, messages: Sequence[Message]) -> dict[str, Any]:
    """One case as the maintainer sees it: the judge's projection, and the writes to label."""
    timeline = tool_timeline(messages)
    tool_names = {record.call.call_id: record.call.name for record in timeline}
    shown = []
    for message, chat in zip(messages, judge_view(messages), strict=True):
        item: dict[str, Any] = {"role": chat["role"], "content": chat["content"]}
        if message.tool_calls:
            item["tool_calls"] = [
                {"name": call.name, "arguments": call.arguments} for call in message.tool_calls
            ]
        if message.role is Role.TOOL:
            item["tool_name"] = tool_names.get(message.tool_call_id)
        shown.append(item)
    writes = [
        {"name": record.call.name, "arguments": record.call.arguments}
        for record in timeline
        if record.call.name in WRITE_TOOLS
    ]
    return {"id": case_id, "messages": shown, "writes": writes}


def rubric_text(version: str) -> str:
    """A judge's prompt without its reply format, which is for the model and not a reviewer."""
    return judge_prompt(version).split(_REPLY_FORMAT)[0].strip()


def worksheet_data(cases: Sequence[Mapping[str, Any]]) -> str:
    """The JSON a worksheet embeds, safe inside a ``<script>`` element."""
    rubrics = {name: rubric_text(version) for name, version in JUDGE_PROMPTS.items()}
    text = json.dumps({"cases": list(cases), "rubrics": rubrics}, ensure_ascii=False)
    return text.replace("</", "<\\/")
