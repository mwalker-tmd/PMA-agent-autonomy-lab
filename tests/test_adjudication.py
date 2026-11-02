import json
from pathlib import Path

import pytest
import yaml

from agent_autonomy_lab.application.adjudication import (
    REQUIRED_POLICY_TASKS,
    rubric_text,
    select_sample,
    worksheet_case,
    worksheet_data,
)
from agent_autonomy_lab.entrypoints import adjudicate
from agent_autonomy_lab.entrypoints.replay_state import saved_simulations

REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
TASK_4 = FIXTURES / "m2-task-4" / "simulation.json"


def _row(task, trial, *, reward=1.0, part_a="PASS", results=()):
    return {
        "task_id": task,
        "trial": trial,
        "simulation_id": f"sim-{task}-{trial}",
        "benchmark": {"reward": reward},
        "roll_ups": {"policy_compliance": part_a},
        "results": [{"evaluation_key": key, "status": status} for key, status in results],
    }


def _task(category="multi_step", risks=()):
    return {"category": category, "risk_cases": list(risks)}


def _population():
    tasks = {
        "10": _task("policy_sensitive", ["escalation"]),
        "50": _task("policy_sensitive", ["escalation"]),
        "57": _task("policy_sensitive"),
        "13": _task("multi_step", ["must_refuse"]),
        **{str(n): _task() for n in range(100, 106)},
        "critical": _task(),
        "wrong": _task(),
        "quiet": _task(),
        "flagged": _task(),
    }
    rows = [_row(task, trial) for task in tasks for trial in range(3)]
    rows[tasks_index(rows, "critical", 0)] = _row(
        "critical", 0, results=[("required_customer_confirmation", "INSUFFICIENT_EVIDENCE")]
    )
    rows[tasks_index(rows, "wrong", 0)] = _row(
        "wrong", 0, reward=0.0, part_a="FAIL", results=[("expected_writes", "FAIL")]
    )
    rows[tasks_index(rows, "quiet", 0)] = _row(
        "quiet", 0, reward=0.0, results=[("expected_writes", "FAIL")]
    )
    rows[tasks_index(rows, "flagged", 0)] = _row("flagged", 0, part_a="FAIL")
    return rows, tasks


def tasks_index(rows, task, trial):
    return next(i for i, r in enumerate(rows) if r["task_id"] == task and r["trial"] == trial)


def _wrote(rows, *writers):
    return {r["simulation_id"]: r["simulation_id"] in writers for r in rows}


def test_every_critical_and_disagreeing_simulation_is_taken():
    rows, tasks = _population()
    cases = select_sample(rows, tasks, _wrote(rows, "sim-wrong-0"), size=12)
    strata = {case.simulation_id: case.strata for case in cases}
    assert "critical_risk" in strata["sim-critical-0"]
    assert "critical_risk" in strata["sim-wrong-0"]
    assert "outcome_conduct_disagreement" in strata["sim-quiet-0"]
    assert "outcome_conduct_disagreement" in strata["sim-flagged-0"]
    assert len(cases) == 12


def test_a_failed_expected_write_is_critical_only_if_a_write_ran():
    rows, tasks = _population()
    cases = select_sample(rows, tasks, _wrote(rows), size=12)
    strata = {case.simulation_id: case.strata for case in cases}
    assert "critical_risk" not in strata["sim-quiet-0"]


def test_the_transfer_tasks_are_always_in_and_filler_is_one_per_task():
    rows, tasks = _population()
    cases = select_sample(rows, tasks, _wrote(rows), size=12)
    chosen = [case.task_id for case in cases]
    assert set(REQUIRED_POLICY_TASKS) <= set(chosen)
    filler = [c.task_id for c in cases if "outcome_conduct_disagreement" not in c.strata]
    filler = [task for task in filler if task != "critical"]
    assert len(filler) == len(set(filler))


def test_the_same_seed_gives_the_same_sample_and_ids_are_in_shuffled_order():
    rows, tasks = _population()
    first = select_sample(rows, tasks, _wrote(rows), size=12)
    assert first == select_sample(rows, tasks, _wrote(rows), size=12)
    assert [case.case_id for case in first] == [f"C{n:02d}" for n in range(1, 13)]


def test_a_sample_too_small_for_its_required_cases_is_refused():
    rows, tasks = _population()
    with pytest.raises(ValueError, match="do not fit"):
        select_sample(rows, tasks, _wrote(rows), size=5)


def test_a_score_does_not_change_the_selection():
    rows, tasks = _population()
    scored = json.loads(json.dumps(rows))
    for row in scored:
        row["results"].append({"evaluation_key": "clarity_customer_effort", "status": "FAIL"})
    assert select_sample(rows, tasks, _wrote(rows), size=12) == select_sample(
        scored, tasks, _wrote(rows), size=12
    )


def _task_4_messages():
    _, (simulation,) = saved_simulations(str(TASK_4))
    return simulation.messages


def test_a_worksheet_case_holds_the_writes_and_nothing_about_the_run():
    case = worksheet_case("C01", _task_4_messages())
    assert set(case) == {"id", "messages", "writes"}
    assert {name for m in case["messages"] for name in m} <= {
        "role",
        "content",
        "tool_calls",
        "tool_name",
    }
    assert len(case["writes"]) == 2
    text = json.dumps(case)
    for word in ("reward", "simulation_id", "task_id", "evaluation_key", "tool_call_id"):
        assert word not in text


def test_a_worksheet_shows_replies_without_the_wrapper():
    case = worksheet_case("C01", _task_4_messages())
    replies = [m["content"] for m in case["messages"] if m["role"] == "assistant" and m["content"]]
    assert replies
    assert not any(reply.startswith('{"message"') for reply in replies)


def test_the_rubric_text_leaves_out_the_reply_format():
    for version in ("clarity-v1", "efficiency-v1", "escalation-v1", "confirmation-v1"):
        text = rubric_text(version)
        assert "**2," in text
        assert "Reply with one JSON object" not in text


def test_embedded_data_cannot_close_its_script_element():
    case = {"id": "C01", "messages": [{"role": "user", "content": "</script>"}], "writes": []}
    text = worksheet_data([case])
    assert "</script>" not in text
    assert json.loads(text.replace("<\\/", "</"))["cases"] == [case]


def test_the_command_writes_a_blind_page_and_keeps_strata_in_the_sample(tmp_path, monkeypatch):
    rows, tasks = _population()
    report = tmp_path / "simulations.jsonl"
    for row in rows:
        row["attempt"] = 0
    report.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    sets = tmp_path / "sets.json"
    listed = [{"id": key, **value} for key, value in tasks.items()]
    sets.write_text(json.dumps({"baseline": {"tasks": listed}}), encoding="utf-8")
    monkeypatch.setattr(adjudicate, "saved_messages", lambda _dir, _row: _task_4_messages())
    monkeypatch.setattr(
        adjudicate,
        "select_sample",
        lambda r, t, w: select_sample(r, t, w, size=12),
    )
    sample, page = tmp_path / "sample.yaml", tmp_path / "page.html"
    code = adjudicate.main(
        [
            "sample",
            str(tmp_path),
            "--sample",
            str(sample),
            "--worksheet",
            str(page),
            "--report",
            str(report),
            "--task-sets",
            str(sets),
        ]
    )
    assert code == 0
    record = yaml.safe_load(sample.read_text(encoding="utf-8"))
    assert len(record["cases"]) == 12
    html = page.read_text(encoding="utf-8")
    assert "__WORKSHEET_DATA__" not in html
    for case in record["cases"]:
        assert case["simulation_id"] not in html
    for stratum in ("critical_risk", "outcome_conduct_disagreement", '"strata"'):
        assert stratum not in html
