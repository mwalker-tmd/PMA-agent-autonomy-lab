"""The ``replay_state`` command over saved run files."""

import json

import pytest

from agent_autonomy_lab.entrypoints import replay_state

INITIAL_HASH = "b25c9cb211f5efcaee5dd646054a73c4a9f43f4f5acc32c10713cd9f9ac20e9c"


def raw_simulation(simulation_id: str = "sim-1", messages: list | None = None) -> dict:
    return {
        "id": simulation_id,
        "task_id": "0",
        "start_time": "2026-10-31T10:00:00.000000",
        "duration": 1.0,
        "termination_reason": "user_stop",
        "messages": messages or [],
    }


def save_single(path, live_hash: str | None = None, messages: list | None = None):
    path.mkdir()
    (path / "simulation.json").write_text(json.dumps(raw_simulation(messages=messages)))
    if live_hash:
        (path / "outcome.json").write_text(json.dumps({"outcome": {"final_db_hash": live_hash}}))
    return path


def run(capsys, *args) -> tuple[int, list[dict]]:
    code = replay_state.main([str(a) for a in args])
    return code, json.loads(capsys.readouterr().out)


def test_a_matching_live_hash_passes(tmp_path, capsys):
    code, rows = run(capsys, save_single(tmp_path / "run", INITIAL_HASH))
    assert code == 0
    assert rows[0]["hash_matches_live"] is True
    assert rows[0]["replayed_hash"] == INITIAL_HASH


def test_a_different_live_hash_fails_and_shows_both(tmp_path, capsys):
    code, rows = run(capsys, save_single(tmp_path / "run", "0" * 64))
    assert code == 1
    assert rows[0]["hash_matches_live"] is False
    assert rows[0]["live_hash"] == "0" * 64


def test_no_recorded_live_hash_is_reported_as_unknown_not_as_a_match(tmp_path, capsys):
    code, rows = run(capsys, save_single(tmp_path / "run"))
    assert code == 0
    assert rows[0]["live_hash"] is None
    assert rows[0]["hash_matches_live"] is None


def test_a_divergence_fails(tmp_path, capsys):
    messages = [
        {
            "role": "assistant",
            "turn_idx": 0,
            "timestamp": "2026-10-31T10:00:00.000000",
            "tool_calls": [
                {
                    "id": "call-0",
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": "#W6779827", "reason": "no longer needed"},
                }
            ],
        }
    ]
    code, rows = run(capsys, save_single(tmp_path / "run", messages=messages))
    assert code == 1
    assert rows[0]["writes"] == [{"tool": "cancel_pending_order", "outcome": "no_result"}]
    assert rows[0]["divergences"][0]["call_id"] == "call-0"


def test_a_results_file_reports_each_simulation(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    data = {"simulations": [raw_simulation("sim-1"), raw_simulation("sim-2")]}
    (run_dir / "results.json").write_text(json.dumps(data))
    code, rows = run(capsys, run_dir)
    assert code == 0
    assert [row["simulation_id"] for row in rows] == ["sim-1", "sim-2"]


def test_a_missing_run_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("TAU2_DATA_DIR", raising=False)
    with pytest.raises(FileNotFoundError):
        replay_state.main([str(tmp_path / "nothing")])
