import json
from pathlib import Path

import httpx
import pytest

from agent_autonomy_lab.adapters.litellm import Cassette, RecordingTransport
from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelResponse
from agent_autonomy_lab.entrypoints import judge_rubrics
from agent_autonomy_lab.ports.model_client import ScriptedModelClient

REPLY = '{"score": 2, "evidence": "Clear.", "uncertain": false}'
TASK_0 = str(Path(__file__).parent / "fixtures" / "m2-task-0" / "simulation.json")


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
            judge_rubrics, "RecordingTransport", lambda: RecordingTransport(_provider(text))
        )

    return use


def test_the_command_reports_results_usage_and_a_recording(recording_to, tmp_path, capsys):
    recording_to('{"score": 2, "evidence": "Clear.", "uncertain": false}')
    recording = tmp_path / "judged.json"

    assert judge_rubrics.main([TASK_0, "--recording", str(recording)]) == 0

    report = json.loads(capsys.readouterr().out)
    totals = report["totals"]
    assert (totals["simulations"], totals["calls"]) == (1, 3)
    assert (totals["input_tokens"], totals["output_tokens"]) == (300, 60)
    assert totals["results_by_judge"]["clarity_customer_effort"] == {"PASS": 1}
    (row,) = report["simulations"]
    assert row["task_id"] == "0"
    assert row["unwrapped_replies"] > 0
    assert row["results"][0]["judge_run"]["prompt_version"] == "clarity-v1"
    assert len(Cassette.load(recording).interactions) == 3
    assert report["recording_sha256"]


def test_the_total_cost_is_unknown_if_any_call_cost_is_unknown():
    priced = ModelResponse(REPLY, "m", 100, 20, 0.5, "end_turn", {})
    unpriced = ModelResponse(REPLY, "m", 100, 20, None, "end_turn", {})

    all_priced = judge_rubrics.report([TASK_0], ScriptedModelClient([priced] * 3))["totals"]
    assert all_priced["cost_usd"] == pytest.approx(1.5)
    assert all_priced["calls_of_unknown_cost"] == 0

    one_unpriced = judge_rubrics.report([TASK_0], ScriptedModelClient([priced, unpriced, priced]))[
        "totals"
    ]
    assert (one_unpriced["cost_usd"], one_unpriced["calls_of_unknown_cost"]) == (None, 1)

    one_failed = judge_rubrics.report(
        [TASK_0], ScriptedModelClient([priced, ModelCallError("no"), priced])
    )["totals"]
    assert (one_failed["cost_usd"], one_failed["calls_of_unknown_cost"]) == (None, 1)


def test_the_command_exits_1_on_an_evaluator_error(recording_to, tmp_path, capsys):
    recording_to("Not JSON.")
    assert judge_rubrics.main([TASK_0, "--recording", str(tmp_path / "judged.json")]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["results_by_judge"]["clarity_customer_effort"] == {"EVALUATOR_ERROR": 1}


def test_the_command_refuses_an_existing_recording_before_any_call(monkeypatch, tmp_path):
    recording = tmp_path / "judged.json"
    recording.write_text("{}", "utf-8")
    monkeypatch.setattr(judge_rubrics, "report", lambda *_: pytest.fail("called a model"))
    with pytest.raises(SystemExit) as refused:
        judge_rubrics.main([TASK_0, "--recording", str(recording)])
    assert refused.value.code == 2
