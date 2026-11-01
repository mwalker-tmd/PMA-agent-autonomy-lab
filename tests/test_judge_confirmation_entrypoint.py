"""The ``judge_confirmation`` command, and the option that adds PA-2 to part A."""

import json
from pathlib import Path

import httpx
import pytest

from agent_autonomy_lab.adapters.litellm import Cassette, RecordingTransport
from agent_autonomy_lab.adapters.tau2.retail_state import ReplayedRetailState
from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelResponse
from agent_autonomy_lab.entrypoints import check_tool_correctness, judge_confirmation
from agent_autonomy_lab.ports.model_client import ScriptedModelClient
from agent_autonomy_lab.ports.reference_writes import CannedReferenceWrites

FIXTURES = Path(__file__).parent / "fixtures"
TASK_4 = str(FIXTURES / "m2-task-4" / "simulation.json")
TASK_2 = str(FIXTURES / "m2-task-2" / "simulation.json")

YES = '{"score": 2, "evidence": "Listed, and the user said yes.", "uncertain": false}'
NO = '{"score": 0, "evidence": "Nothing was listed.", "uncertain": false}'


def _provider(text: str):
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": json.loads(request.content)["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    return httpx.MockTransport(reply)


@pytest.fixture
def recording_to(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-offline-test")

    def use(text: str) -> None:
        monkeypatch.setattr(
            judge_confirmation, "RecordingTransport", lambda: RecordingTransport(_provider(text))
        )

    return use


def test_the_report_gives_each_writes_result_binding_and_roll_up():
    client = ScriptedModelClient(
        [
            ModelResponse(NO, "the-model", 100, 20, 0.5, "end_turn", {"max_tokens": 1}),
            ModelResponse(YES, "the-model", 100, 20, 0.25, "end_turn", {"max_tokens": 1}),
        ]
    )
    report = judge_confirmation.report([TASK_4], client)
    totals = report["totals"]
    assert (totals["simulations"], totals["calls"]) == (1, 2)
    assert (totals["input_tokens"], totals["output_tokens"]) == (200, 40)
    assert totals["cost_usd"] == 0.75
    assert totals["results"] == {"FAIL": 1, "PASS": 1}
    (row,) = report["simulations"]
    assert (row["task_id"], row["confirmation"]) == ("4", "FAIL")
    first, second = row["results"]
    assert (first["status"], first["score"], first["failure_kind"]) == ("FAIL", 0, "violation")
    assert second["status"] == "PASS"
    span = first["bound_span"]
    assert span["tool"] == "modify_pending_order_items"
    assert span["run_id"] == first["target"]
    assert span["turn_index"] == 23
    assert span["argument_hash"].startswith("sha256:")


def test_the_cost_is_null_if_any_calls_cost_is_unknown():
    client = ScriptedModelClient(
        [
            ModelResponse(NO, "the-model", 100, 20, 0.5, "end_turn", {"max_tokens": 1}),
            ModelResponse(YES, "the-model", 100, 20, None, "end_turn", {"max_tokens": 1}),
        ]
    )
    totals = judge_confirmation.report([TASK_4], client)["totals"]
    assert (totals["cost_usd"], totals["calls_of_unknown_cost"]) == (None, 1)


def test_a_fail_does_not_change_the_exit_code_and_the_calls_are_recorded(
    recording_to, tmp_path, capsys
):
    recording_to(NO)
    recording = tmp_path / "judged.json"
    assert judge_confirmation.main([TASK_4, "--recording", str(recording)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["results"] == {"FAIL": 2}
    assert len(Cassette.load(recording).interactions) == 2
    assert report["recording_sha256"]


def test_an_evaluator_error_exits_1(recording_to, tmp_path, capsys):
    recording_to("not JSON")
    assert judge_confirmation.main([TASK_4, "--recording", str(tmp_path / "judged.json")]) == 1
    assert json.loads(capsys.readouterr().out)["totals"]["results"] == {"EVALUATOR_ERROR": 2}


def test_an_existing_recording_is_refused_before_any_model_call(monkeypatch, tmp_path):
    existing = tmp_path / "judged.json"
    existing.write_text("{}")
    monkeypatch.setattr(judge_confirmation, "report", lambda *_: pytest.fail("a model was called"))
    with pytest.raises(SystemExit):
        judge_confirmation.main([TASK_4, "--recording", str(existing)])


def test_a_failed_call_has_no_usage_to_count():
    client = ScriptedModelClient([ModelCallError("refused"), ModelCallError("refused")])
    totals = judge_confirmation.report([TASK_4], client)["totals"]
    assert totals["results"] == {"EVALUATOR_ERROR": 2}
    assert (totals["calls"], totals["cost_usd"]) == (2, None)


# Part A with and without the judge.


def _rows(client):
    return check_tool_correctness.report(
        [TASK_4], ReplayedRetailState(), CannedReferenceWrites(), client
    )


def test_part_a_says_whether_confirmation_was_judged():
    (row,) = _rows(None)
    assert row["tool_correctness"]["confirmation_judged"] is False
    assert row["tool_correctness"]["prohibited_actions"] == "PASS"
    assert "required_customer_confirmation" not in row["checks"]


def test_with_the_judge_part_a_fails_a_write_with_no_confirmation():
    (row,) = _rows(ScriptedModelClient([NO, YES]))
    assert row["tool_correctness"]["confirmation_judged"] is True
    assert row["tool_correctness"]["prohibited_actions"] == "FAIL"
    assert row["checks"]["required_customer_confirmation"] == {"FAIL": 1, "PASS": 1}
    (concern,) = row["concerns"]
    assert concern["check"] == "required_customer_confirmation"


def test_no_model_is_called_without_the_option(monkeypatch, capsys):
    monkeypatch.setattr(
        check_tool_correctness, "LiteLLMModelClient", lambda: pytest.fail("a client was built")
    )
    check_tool_correctness.main([TASK_2])
    assert (
        json.loads(capsys.readouterr().out)[0]["tool_correctness"]["confirmation_judged"] is False
    )
