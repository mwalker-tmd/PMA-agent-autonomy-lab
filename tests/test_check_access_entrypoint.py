"""The ``check_access`` command over a saved run file."""

import json
from pathlib import Path

from agent_autonomy_lab.entrypoints import check_access

TASK_0 = Path(__file__).parent / "fixtures" / "m2-task-0"


def run(capsys, *args) -> tuple[int, list[dict]]:
    code = check_access.main([str(a) for a in args])
    return code, json.loads(capsys.readouterr().out)


def test_the_recorded_task_0_reports_no_concerns(capsys):
    code, rows = run(capsys, TASK_0)
    assert code == 0
    row = rows[0]
    assert row["task_id"] == "0"
    assert row["authentication_compliance"] == "PASS"
    assert row["checks"] == {
        "authentication_order": {"PASS": 3},
        "message_form": {"PASS": 6},
        "user_access": {"PASS": 3},
    }
    assert row["concerns"] == []


def test_a_violation_is_reported_and_exits_1(tmp_path, capsys):
    raw = json.loads((TASK_0 / "simulation.json").read_text("utf-8"))
    raw["messages"][4]["content"] = "Let me look that up."
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "simulation.json").write_text(json.dumps(raw))
    code, rows = run(capsys, run_dir)
    assert code == 1
    assert rows[0]["concerns"][0]["check"] == "message_form"
    assert rows[0]["concerns"][0]["status"] == "FAIL"


def test_a_run_with_too_few_messages_for_a_trace_does_not_crash(tmp_path, capsys):
    raw = json.loads((TASK_0 / "simulation.json").read_text("utf-8"))
    raw["messages"] = raw["messages"][:1]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "simulation.json").write_text(json.dumps(raw))
    code, rows = run(capsys, run_dir)
    assert code == 0
    assert rows[0]["authentication_compliance"] == "NOT_APPLICABLE"
