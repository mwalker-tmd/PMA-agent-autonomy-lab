import json
from pathlib import Path

import pytest
import yaml

from agent_autonomy_lab.application.adjudication import (
    JUDGE_PROMPTS,
    REQUIRED_POLICY_TASKS,
    compare_labels,
    metric_counts,
    rubric_text,
    select_sample,
    self_agreement,
    with_rescored_rubrics,
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
    for version in JUDGE_PROMPTS.values():
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


def _label(**fields):
    base = {
        "case_id": "C01",
        "clarity": "2",
        "efficiency": "2",
        "escalation": "2",
        "confirmation_by_write": [],
        "handled_correctly": "yes",
        "note": None,
    }
    return {**base, **fields}


def _judged(key, status, score=None, target=None):
    return {
        "evaluation_key": key,
        "status": status,
        "score": score,
        "reason": "because",
        "target": target,
    }


CASE = {"case_id": "C01", "task_id": "4", "trial": 0, "simulation_id": "sim"}


def _compared(label, *results):
    report = compare_labels([label], [CASE], {"sim": {"results": list(results)}})
    return report["judges"], report["escalation_applicability_differences"]


def test_a_judge_fail_the_maintainer_scored_2_is_a_false_positive():
    judges, _ = _compared(_label(), _judged("clarity_customer_effort", "FAIL", 1))
    entry = judges["clarity_customer_effort"]
    assert (entry["compared"], len(entry["false_positives"]), entry["same_verdict"]) == (1, 1, 0)


def test_a_judge_pass_the_maintainer_scored_low_is_a_false_negative():
    judges, _ = _compared(_label(efficiency="1"), _judged("conversation_efficiency", "PASS", 2))
    assert len(judges["conversation_efficiency"]["false_negatives"]) == 1


def test_two_failing_scores_agree_on_the_verdict_but_not_the_score():
    judges, _ = _compared(_label(clarity="0"), _judged("clarity_customer_effort", "FAIL", 1))
    entry = judges["clarity_customer_effort"]
    assert (entry["same_verdict"], entry["same_score"]) == (1, 0)
    assert len(entry["score_only_differences"]) == 1


def test_no_transfer_needed_and_a_2_agree_but_count_as_an_applicability_difference():
    judges, differences = _compared(
        _label(escalation="no_transfer_needed"), _judged("escalation_correctness", "PASS", 2)
    )
    assert judges["escalation_correctness"]["same_verdict"] == 1
    assert len(differences) == 1


def test_a_judge_not_applicable_matches_no_transfer_needed():
    judges, differences = _compared(
        _label(escalation="no_transfer_needed"),
        _judged("escalation_correctness", "NOT_APPLICABLE"),
    )
    assert judges["escalation_correctness"]["same_score"] == 1
    assert differences == []


def test_a_result_with_no_rubric_score_is_not_compared():
    judges, _ = _compared(
        _label(),
        _judged("escalation_correctness", "FAIL"),
        _judged("clarity_customer_effort", "EVALUATOR_ERROR"),
    )
    assert judges["escalation_correctness"]["compared"] == 0
    assert len(judges["escalation_correctness"]["no_rubric_score"]) == 1
    assert len(judges["clarity_customer_effort"]["no_rubric_score"]) == 1


def test_a_label_of_cant_tell_is_not_compared():
    judges, _ = _compared(_label(clarity="unclear"), _judged("clarity_customer_effort", "PASS", 2))
    entry = judges["clarity_customer_effort"]
    assert (entry["compared"], len(entry["human_unclear"])) == (0, 1)


def test_writes_are_matched_by_their_order():
    judges, _ = _compared(
        _label(confirmation_by_write=["2", "0"]),
        _judged("required_customer_confirmation", "PASS", 2, target="a"),
        _judged("required_customer_confirmation", "FAIL", 0, target="b"),
    )
    assert judges["required_customer_confirmation"]["same_score"] == 2


def _repeat(run, status, score, key="clarity_customer_effort"):
    return {
        "simulation_id": "sim",
        "run": run,
        "evaluation_key": key,
        "unit": 0,
        "status": status,
        "score": score,
    }


def test_self_agreement_counts_repeats_that_match_and_those_that_do_not():
    rows = {"sim": {"results": [_judged("clarity_customer_effort", "PASS", 2)]}}
    same = self_agreement([_repeat(n, "PASS", 2) for n in range(3)], rows)
    assert same["clarity_customer_effort"]["same_in_all_runs"] == 1
    assert same["clarity_customer_effort"]["also_same_as_baseline"] == 1
    mixed = self_agreement(
        [_repeat(0, "PASS", 2), _repeat(1, "FAIL", 1), _repeat(2, "PASS", 2)], rows
    )
    assert mixed["clarity_customer_effort"]["same_in_all_runs"] == 0
    assert len(mixed["clarity_customer_effort"]["unstable"]) == 1


def test_rescoring_replaces_only_the_rubric_results():
    row = {
        "results": [
            _judged("clarity_customer_effort", "FAIL", 1),
            _judged("required_customer_confirmation", "PASS", 2, target="a"),
        ]
    }
    after = with_rescored_rubrics(row, [_judged("clarity_customer_effort", "PASS", 2)])
    statuses = {r["evaluation_key"]: r["status"] for r in after["results"]}
    assert statuses == {
        "required_customer_confirmation": "PASS",
        "clarity_customer_effort": "PASS",
    }


def test_metric_counts_leave_other_statuses_out_of_the_denominator():
    rows = [
        {"results": [_judged("clarity_customer_effort", status)]}
        for status in ("PASS", "FAIL", "NOT_APPLICABLE", "EVALUATOR_ERROR", "INSUFFICIENT_EVIDENCE")
    ]
    assert metric_counts(rows, "clarity_customer_effort") == {
        "numerator": 1,
        "denominator": 2,
        "not_applicable": 1,
        "insufficient_evidence": 1,
        "evaluator_error": 1,
    }


def test_a_saved_run_that_is_not_the_sampled_simulation_is_refused(tmp_path):
    directory = tmp_path / "task-4-trial-0-attempt-1"
    directory.mkdir()
    (directory / "simulation.json").write_text(TASK_4.read_text("utf-8"), encoding="utf-8")
    row = {"task_id": "4", "trial": 0, "attempt": 1, "simulation_id": "someone-else"}
    with pytest.raises(ValueError, match="not someone-else"):
        adjudicate.sampled_subject(tmp_path, row)
    _, (simulation,) = saved_simulations(str(TASK_4))
    row["simulation_id"] = simulation.simulation_id
    assert adjudicate.sampled_subject(tmp_path, row).simulation_id == simulation.simulation_id


def test_the_worksheet_exports_what_compare_reads_and_waits_for_saved_labels():
    page = adjudicate.render_worksheet([])
    for field in ("confirmation_by_write:", "note: l.note || null", "{ labels: CASES"):
        assert field in page
    assert page.index('$("sheet").inert = true') < page.index("db = api")
    assert page.index('.collection("labels").get()') < page.index("db = api")
