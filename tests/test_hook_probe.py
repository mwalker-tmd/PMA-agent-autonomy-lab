"""The hook probe on a scripted cassette of real τ-bench retail task 0: offline."""

import json
from uuid import uuid4

import litellm
import pytest

from agent_autonomy_lab.adapters.litellm import RecordingTransport
from agent_autonomy_lab.adapters.tau2 import live_run
from agent_autonomy_lab.entrypoints import cassette_run, hook_probe
from tests.fake_provider import ScriptedProvider, task_0_outputs

CASSETTE = "cassettes/task-0.cassette.json"


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    """A recorded cassette and its outcome.json; every callback removed afterwards."""
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-key")
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider(task_0_outputs())
    monkeypatch.setattr(cassette_run, "RecordingTransport", lambda: RecordingTransport(provider))
    monkeypatch.setattr(
        cassette_run,
        "register_untraced_agent",
        lambda: live_run.register_untraced_agent(f"test_probe_{uuid4().hex[:8]}"),
    )
    monkeypatch.setattr(hook_probe, "COUNTER", hook_probe.CallCounter())
    assert cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"]) == 0
    yield tmp_path / "simulations" / "rec" / "outcome.json"
    litellm.logging_callback_manager._reset_all_callbacks()


def probe(capsys, mode: str, reference) -> dict:
    capsys.readouterr()
    args = [mode, "--cassette", CASSETTE, "--repeats", "1", "--reference", str(reference)]
    assert hook_probe.main(args) == 0
    return json.loads(capsys.readouterr().out)


def test_the_callback_sees_every_model_call_and_changes_nothing(recorded, capsys):
    report = probe(capsys, "c", recorded)

    assert report["equal_to_reference"] is True
    assert report["served"] == [4]
    assert report["callback"]["calls_per_replay"] == [4]
    assert report["callback"]["calls_without_cost"] == 0


def test_the_callback_sees_the_nl_judge(recorded, capsys):
    judge = probe(capsys, "c", recorded)["judge"]

    (call,) = judge["callback_saw"]
    assert call["model"] == judge["judge_model"]
    assert call["prompt"] == hook_probe.CANNED_JUDGE_TOKENS["input_tokens"]
    assert call["completion"] == hook_probe.CANNED_JUDGE_TOKENS["output_tokens"]


def test_the_tool_wrapper_sees_each_call_and_changes_nothing(recorded, capsys):
    report = probe(capsys, "e", recorded)

    assert report["equal_to_reference"] is True
    assert [call["name"] for call in report["tool_calls"]] == ["find_user_id_by_name_zip"]
    assert report["tool_calls"][0]["error"] is False


def test_nothing_is_added_in_plain_mode(recorded, capsys):
    report = probe(capsys, "plain", recorded)

    assert report["equal_to_reference"] is True
    assert "callback" not in report
    assert "tool_calls" not in report


def test_an_outcome_other_than_the_reference_fails(recorded, capsys):
    changed = json.loads(recorded.read_text(encoding="utf-8"))
    changed["outcome"]["final_db_hash"] = "another database"
    recorded.write_text(json.dumps(changed), encoding="utf-8")

    args = ["plain", "--cassette", CASSETTE, "--repeats", "1", "--reference", str(recorded)]
    capsys.readouterr()
    assert hook_probe.main(args) == 1
    assert json.loads(capsys.readouterr().out)["equal_to_reference"] is False


def test_a_missed_model_call_fails(recorded, capsys, monkeypatch):
    monkeypatch.setattr(hook_probe, "CALLBACK_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(hook_probe.CallCounter, "log_success_event", lambda *args: None)

    assert hook_probe.main(["c", "--cassette", CASSETTE, "--repeats", "1"]) == 1


def test_a_missing_cassette_is_refused(tmp_path, capsys):
    assert hook_probe.main(["plain", "--cassette", str(tmp_path / "none.json")]) == 1
    assert "is not a cassette file" in capsys.readouterr().err
