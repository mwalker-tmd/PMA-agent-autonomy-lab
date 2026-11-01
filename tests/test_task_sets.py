"""The Milestone 4 task sets hold their rules against τ-bench's real tasks at the pin.

The held-out checks need only each task's ID and user, which are worked out here
and never printed, so these tests do not inspect what a held-out task asks.
"""

import hashlib
import json
from collections import Counter
from importlib import metadata
from pathlib import Path

import pytest
from tau2.domains.retail.environment import get_tasks
from tau2.domains.retail.utils import RETAIL_DB_PATH, RETAIL_TASK_SET_PATH

from agent_autonomy_lab.domain.reference_writes import DEFECT_TASKS

DEFINITION = Path(__file__).parent.parent / "datasets" / "m4-task-sets-v1.json"
REQUIRED_BASELINE_TASKS = {"0", "4", "13", "88", "113", "43", "10", "50", "67", "68"}
WORKFLOWS = {"lookup", "cancellation", "return_exchange", "modification", "hand_off"}
CATEGORIES = {
    "straightforward",
    "multi_step",
    "ambiguous",
    "difficult_contradictory",
    "policy_sensitive",
}
RISK_CASES = {
    "must_refuse",
    "escalation",
    "confirmation_turn",
    "wrong_identity_details",
    "ineligible_order",
    "overwritten_write",
    "withheld_detail",
}


@pytest.fixture(scope="module")
def definition() -> dict:
    return json.loads(DEFINITION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def baseline(definition) -> list[dict]:
    return definition["baseline"]["tasks"]


@pytest.fixture(scope="module")
def tasks() -> dict:
    return {task.id: task for task in get_tasks(task_split_name=None)}


@pytest.fixture(scope="module")
def split_test_ids() -> set[str]:
    return {task.id for task in get_tasks(task_split_name="test")}


@pytest.fixture(scope="module")
def db() -> dict:
    return json.loads(Path(RETAIL_DB_PATH).read_text(encoding="utf-8"))


def task_users(task, db: dict) -> set[str]:
    """The users a task concerns: from its reference first, else its scenario's identity."""
    users = set()
    for action in task.evaluation_criteria.actions or []:
        arguments = action.arguments or {}
        if arguments.get("user_id") in db["users"]:
            users.add(arguments["user_id"])
        if arguments.get("order_id") in db["orders"]:
            users.add(db["orders"][arguments["order_id"]]["user_id"])
    if users:
        return users
    text = task.user_scenario.instructions.model_dump_json().lower()
    for user_id, user in db["users"].items():
        name = user["name"]
        by_name_zip = (
            name["first_name"].lower() in text
            and name["last_name"].lower() in text
            and user["address"]["zip"] in text
        )
        if by_name_zip or user["email"].lower() in text:
            users.add(user_id)
    return users


def test_the_code_is_installed_at_the_recorded_commit(definition):
    direct_url = json.loads(metadata.distribution("tau2").read_text("direct_url.json"))
    assert direct_url["vcs_info"]["commit_id"] == definition["tau_bench_commit"]
    assert definition["tau_bench_commit"].startswith("0c69df4")


def test_the_loaded_data_files_match_the_recorded_commit(definition):
    """τ-bench reads its data from ``TAU2_DATA_DIR``, separately from the installed code."""
    loaded = {path.name: path for path in map(Path, (RETAIL_TASK_SET_PATH, RETAIL_DB_PATH))}
    loaded["split_tasks.json"] = Path(RETAIL_TASK_SET_PATH).with_name("split_tasks.json")
    assert definition["source_sha256"] == {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in loaded.items()
    }


def test_every_task_id_exists(definition, baseline, tasks):
    assert {entry["id"] for entry in baseline} <= set(tasks)
    assert set(definition["held_out"]["task_ids"]) <= set(tasks)


def test_every_task_resolves_to_exactly_one_user(tasks, db):
    assert {task_id: 1 for task_id in tasks} == {
        task_id: len(task_users(task, db)) for task_id, task in tasks.items()
    }


def test_the_baseline_has_about_twenty_tasks_and_every_required_one(baseline):
    ids = [entry["id"] for entry in baseline]
    assert len(ids) == len(set(ids)) == 20
    assert REQUIRED_BASELINE_TASKS <= set(ids)


def test_67_and_68_count_as_one_scenario(definition, baseline):
    scenarios = {entry["id"]: entry["scenario"] for entry in baseline}
    assert scenarios["67"] == scenarios["68"]
    assert len(set(scenarios.values())) == definition["baseline"]["counts"]["scenarios"] == 19


def test_no_user_has_more_than_two_baseline_tasks(baseline, tasks, db):
    for entry in baseline:
        assert task_users(tasks[entry["id"]], db) == {entry["user"]}
    assert max(Counter(entry["user"] for entry in baseline).values()) == 2


def test_only_68_comes_from_the_test_split(baseline, split_test_ids):
    for entry in baseline:
        assert entry["split"] == ("test" if entry["id"] in split_test_ids else "train")
    assert {entry["id"] for entry in baseline} & split_test_ids == {"68"}


def test_defect_tasks_are_in_neither_set(definition, baseline):
    assert set(definition["excluded_defect_tasks"]) == DEFECT_TASKS
    assert not {entry["id"] for entry in baseline} & DEFECT_TASKS
    assert not set(definition["held_out"]["task_ids"]) & DEFECT_TASKS


def test_the_recorded_counts_match_the_tasks(definition, baseline, tasks):
    counts = definition["baseline"]["counts"]
    assert counts["tasks"] == len(baseline)
    assert counts["users"] == len({entry["user"] for entry in baseline})
    assert counts["by_workflow"] == Counter(entry["workflow"] for entry in baseline)
    assert counts["by_category"] == Counter(entry["category"] for entry in baseline)
    assert counts["by_risk_case"] == Counter(
        risk_case for entry in baseline for risk_case in entry["risk_cases"]
    )
    assert counts["with_nl_assertions"] == sum(
        bool(tasks[entry["id"]].evaluation_criteria.nl_assertions) for entry in baseline
    )


def test_each_workflow_category_and_risk_case_has_a_task(definition, baseline):
    assert {entry["workflow"] for entry in baseline} == WORKFLOWS
    assert {entry["category"] for entry in baseline} == CATEGORIES
    assert {risk_case for entry in baseline for risk_case in entry["risk_cases"]} == RISK_CASES
    assert set(definition["baseline"]["not_represented"]) == {"tool_exception"}


def test_the_refusal_with_no_write_has_an_empty_reference(tasks):
    assert not tasks["57"].evaluation_criteria.actions


def test_every_baseline_task_states_its_reason(baseline):
    assert all(entry["reason"].strip() for entry in baseline)


def test_the_held_out_set_follows_its_rule(definition, baseline, tasks, split_test_ids, db):
    baseline_ids = {entry["id"] for entry in baseline}
    baseline_users = {entry["user"] for entry in baseline}
    sharing = {
        task_id
        for task_id in split_test_ids - baseline_ids - DEFECT_TASKS
        if task_users(tasks[task_id], db) & baseline_users
    }
    held_out = definition["held_out"]
    assert set(held_out["task_ids"]) == split_test_ids - baseline_ids - DEFECT_TASKS - sharing
    assert held_out["removed_from_test_split"] == {
        "in_the_baseline": sorted(split_test_ids & baseline_ids, key=int),
        "defect_task": sorted(split_test_ids & DEFECT_TASKS, key=int),
        "shares_a_baseline_user": sorted(sharing, key=int),
    }


def test_the_sets_share_no_task_and_no_user(definition, baseline, tasks, db):
    held_out = set(definition["held_out"]["task_ids"])
    assert not held_out & {entry["id"] for entry in baseline}
    held_out_users = {user for task_id in held_out for user in task_users(tasks[task_id], db)}
    assert not held_out_users & {entry["user"] for entry in baseline}
