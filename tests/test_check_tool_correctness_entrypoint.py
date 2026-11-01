"""The ``check_tool_correctness`` command over saved run files."""

import json
from pathlib import Path

from agent_autonomy_lab.entrypoints import check_tool_correctness

FIXTURES = Path(__file__).parent / "fixtures"


def _run(capsys, *args) -> tuple[int, list[dict]]:
    code = check_tool_correctness.main([str(a) for a in args])
    return code, json.loads(capsys.readouterr().out)


def _saved(tmp_path, raw: dict) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "simulation.json").write_text(json.dumps(raw))
    return run_dir


def _task_2() -> dict:
    return json.loads((FIXTURES / "m2-task-2" / "simulation.json").read_text("utf-8"))


def test_the_benchmark_and_the_lab_checks_are_reported_apart(capsys):
    code, rows = _run(capsys, FIXTURES / "m2-task-2")
    assert code == 0
    row = rows[0]
    assert set(row) == {
        "run",
        "simulation_id",
        "task_id",
        "benchmark",
        "tool_correctness",
        "checks",
        "concerns",
    }
    assert "task_success" not in row["checks"]
    assert row["tool_correctness"] == {"prohibited_actions": "PASS", "expected_writes": "PASS"}
    assert row["concerns"] == []


def test_the_fixture_holds_no_reward_so_the_benchmark_says_so(capsys):
    _, rows = _run(capsys, FIXTURES / "m2-task-2")
    assert rows[0]["benchmark"] == {
        "task_success": "INSUFFICIENT_EVIDENCE",
        "termination_reason": rows[0]["benchmark"]["termination_reason"],
        "reward": None,
    }


def test_a_low_reward_is_reported_and_does_not_change_the_exit_code(tmp_path, capsys):
    raw = _task_2()
    raw["reward_info"] = {
        "reward": 0.0,
        "reward_basis": ["DB", "NL_ASSERTION"],
        "reward_breakdown": {"DB": 1.0, "NL_ASSERTION": 0.0},
        "db_check": {"db_match": True, "db_reward": 1.0},
        "nl_assertions": [{"nl_assertion": "x", "met": False, "justification": "y"}],
    }
    code, rows = _run(capsys, _saved(tmp_path, raw))
    assert code == 0
    assert rows[0]["benchmark"]["task_success"] == "FAIL"
    assert rows[0]["benchmark"]["reward_breakdown"] == {"DB": 1.0, "NL_ASSERTION": 0.0}
    assert rows[0]["tool_correctness"]["prohibited_actions"] == "PASS"


def test_a_violation_exits_1_and_a_reward_of_one_does_not_hide_it(tmp_path, capsys):
    raw = _task_2()
    write = next(m for m in raw["messages"] if "return_delivered" in str(m.get("tool_calls")))
    write["tool_calls"][0]["arguments"]["payment_method_id"] = "paypal_0000000"
    raw["reward_info"] = {"reward": 1.0, "reward_basis": ["DB"], "reward_breakdown": {"DB": 1.0}}
    code, rows = _run(capsys, _saved(tmp_path, raw))
    assert code == 1
    assert rows[0]["benchmark"]["task_success"] == "PASS"
    assert rows[0]["tool_correctness"]["prohibited_actions"] == "FAIL"
    assert {c["check"] for c in rows[0]["concerns"] if c["status"] == "FAIL"} >= {
        "refund_method",
        "method_owner",
    }
