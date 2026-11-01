"""The ``check_writes`` command over a saved run file."""

import json
from pathlib import Path

from agent_autonomy_lab.entrypoints import check_writes

FIXTURES = Path(__file__).parent / "fixtures"


def run(capsys, *args) -> tuple[int, list[dict]]:
    code = check_writes.main([str(a) for a in args])
    return code, json.loads(capsys.readouterr().out)


def test_the_recorded_task_2_reports_no_concerns(capsys):
    code, rows = run(capsys, FIXTURES / "m2-task-2")
    assert code == 0
    assert rows[0]["task_id"] == "2"
    assert rows[0]["checks"] == {
        "gift_card_balance": {"NOT_APPLICABLE": 1},
        "item_change_once": {"NOT_APPLICABLE": 1},
        "item_variant": {"NOT_APPLICABLE": 1},
        "order_status": {"PASS": 1},
        "payment_change": {"NOT_APPLICABLE": 1},
        "refund_method": {"PASS": 1},
        "status_read": {"PASS": 1},
    }
    assert rows[0]["concerns"] == []


def test_a_violation_is_reported_and_exits_1(tmp_path, capsys):
    raw = json.loads((FIXTURES / "m2-task-2" / "simulation.json").read_text("utf-8"))
    write = next(m for m in raw["messages"] if "return_delivered" in str(m.get("tool_calls")))
    write["tool_calls"][0]["arguments"]["payment_method_id"] = "paypal_0000000"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "simulation.json").write_text(json.dumps(raw))
    code, rows = run(capsys, run_dir)
    assert code == 1
    concerns = {(c["check"], c["status"]) for c in rows[0]["concerns"]}
    assert ("refund_method", "FAIL") in concerns
