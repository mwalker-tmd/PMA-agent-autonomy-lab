import json
from pathlib import Path

import httpx
import litellm
import pytest

from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    RecordingTransport,
    litellm_transport,
    replaying,
)
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.domain.evaluation import JUDGE_MODEL, JUDGE_SETTINGS, judge_request
from agent_autonomy_lab.domain.model_calls import (
    ModelCallError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from agent_autonomy_lab.entrypoints import judge_probe
from agent_autonomy_lab.entrypoints.judge_probe import SYSTEM, USER
from agent_autonomy_lab.ports.model_client import ModelClient, ScriptedModelClient

RECORDING = Path(__file__).parent / "fixtures" / "judge-probe" / "claude-haiku-5-5.json"
USER_MESSAGE = (ModelMessage("user", "Judge this."),)


def anthropic_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": json.loads(request.content)["model"],
            "content": [{"type": "text", "text": '{"score": 2}'}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    )


@pytest.fixture
def provider(monkeypatch):
    """A scripted Anthropic behind LiteLLM, with τ-bench's global setting off."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-offline-test")
    monkeypatch.setattr(litellm, "drop_params", False)
    recorder = RecordingTransport(httpx.MockTransport(anthropic_reply))
    with litellm_transport(recorder):
        yield recorder


def request_with(**settings) -> ModelRequest:
    return ModelRequest(JUDGE_MODEL, "Rubric.", USER_MESSAGE, settings)


# The judge's settings, on the recording of a real provider call.


def test_the_recorded_judge_request_carries_every_declared_setting_and_nothing_else():
    (interaction,) = Cassette.load(RECORDING).interactions
    body = interaction.request.body
    settings = {k: v for k, v in body.items() if k not in ("model", "messages", "system")}
    assert settings == JUDGE_SETTINGS
    assert body["model"] == JUDGE_MODEL
    assert interaction.response.status == 200


def test_the_recorded_judge_call_replays_with_no_model_call():
    with replaying(Cassette.load(RECORDING)) as transport:
        response = LiteLLMModelClient().complete(judge_request(SYSTEM, USER))
    transport.check_complete()
    assert json.loads(response.text) == {"score": 2}
    assert response.settings_sent == JUDGE_SETTINGS
    assert response.input_tokens > 0 and response.output_tokens > 0


def test_the_recorded_judge_call_is_priced_at_the_list_price():
    with replaying(Cassette.load(RECORDING)):
        response = LiteLLMModelClient().complete(judge_request(SYSTEM, USER))
    list_price = response.input_tokens * 0.10 / 1e6 + response.output_tokens * 0.50 / 1e6
    assert response.cost_usd == pytest.approx(list_price)


# A setting the route drops or changes fails the call.


def test_the_settings_reported_are_the_ones_in_the_request_sent(provider):
    response = LiteLLMModelClient().complete(judge_request("Rubric.", "Evidence."))
    body = provider.interactions[-1].request.body
    assert response.settings_sent == {
        k: v for k, v in body.items() if k not in ("model", "messages", "system")
    }
    assert response.settings_sent == JUDGE_SETTINGS


def test_a_setting_dropped_under_tau2s_global_drop_params_fails(provider, monkeypatch):
    monkeypatch.setattr(litellm, "drop_params", True)
    with pytest.raises(ModelCallError, match="not sent as declared: seed"):
        LiteLLMModelClient().complete(request_with(max_tokens=64, seed=1))
    assert "seed" not in provider.interactions[-1].request.body


def test_judge_settings_arrive_under_tau2s_global_drop_params(provider, monkeypatch):
    monkeypatch.setattr(litellm, "drop_params", True)
    response = LiteLLMModelClient().complete(judge_request("Rubric.", "Evidence."))
    assert response.settings_sent == JUDGE_SETTINGS


def test_an_unsupported_setting_fails_with_no_request_when_nothing_drops_it(provider):
    with pytest.raises(ModelCallError, match="seed"):
        LiteLLMModelClient().complete(request_with(max_tokens=64, seed=1))
    assert provider.interactions == []


def test_reasoning_effort_is_not_sent_as_declared(provider):
    with pytest.raises(ModelCallError, match="not sent as declared: reasoning_effort"):
        LiteLLMModelClient().complete(request_with(max_tokens=2048, reasoning_effort="low"))
    assert "thinking" in provider.interactions[-1].request.body


# Failures and the Human message.


def test_a_provider_error_is_a_model_call_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-offline-test")
    refused = httpx.MockTransport(
        lambda request: httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "no"}}
        )
    )
    with litellm_transport(refused):
        with pytest.raises(ModelCallError, match="BadRequestError"):
            LiteLLMModelClient().complete(judge_request("Rubric.", "Evidence."))


@pytest.mark.parametrize(
    "messages",
    [
        (),
        (ModelMessage("assistant", "A prefill."),),
        USER_MESSAGE + (ModelMessage("assistant", "A"),),
    ],
)
def test_a_request_that_does_not_end_with_a_user_message_is_refused(messages):
    with pytest.raises(ValueError, match="end with a user message"):
        ModelRequest(JUDGE_MODEL, "Rubric.", messages)


def test_a_judge_request_has_the_declared_model_settings_and_a_human_message():
    request = judge_request("Rubric.", "Evidence.")
    assert request.model == JUDGE_MODEL == "claude-haiku-5-5"
    assert request.settings == JUDGE_SETTINGS
    assert request.system == "Rubric."
    assert request.messages == (ModelMessage("user", "Evidence."),)


# The test double.


def test_the_scripted_client_answers_in_order_and_records_what_it_was_asked():
    full = ModelResponse("two", "m", 1, 2, 0.5, "stop")
    client: ModelClient = ScriptedModelClient(["one", full, ModelCallError("three")])
    request = judge_request("Rubric.", "Evidence.")

    first = client.complete(request)
    assert (first.text, first.model) == ("one", JUDGE_MODEL)
    assert first.settings_sent == JUDGE_SETTINGS
    assert client.complete(request) is full
    with pytest.raises(ModelCallError, match="three"):
        client.complete(request)
    with pytest.raises(ModelCallError, match="no scripted reply for request 4"):
        client.complete(request)
    assert client.requests == [request] * 4


# The probe that made the recording.


def test_the_probe_reports_the_call_and_refuses_an_existing_recording_before_any_call(
    provider, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        judge_probe,
        "RecordingTransport",
        lambda: RecordingTransport(httpx.MockTransport(anthropic_reply)),
    )
    recording = tmp_path / "probe.json"

    assert judge_probe.main(["--recording", str(recording)]) == 0

    report = json.loads(capsys.readouterr().out)
    declared = report["declared_call"]
    assert declared["declared_settings"] == declared["effective_settings"] == JUDGE_SETTINGS
    assert declared["litellm_cost_usd"] == pytest.approx(declared["list_price_cost_usd"])
    assert report["undeclared_calls"]["reasoning_effort"]["result"] == "failed"
    assert report["undeclared_calls"]["temperature"]["result"] == "accepted"
    assert Cassette.load(recording).interactions

    calls = len(provider.interactions)
    monkeypatch.setattr(judge_probe, "RecordingTransport", lambda: pytest.fail("called a model"))
    with pytest.raises(SystemExit) as refused:
        judge_probe.main(["--recording", str(recording)])
    assert refused.value.code == 2
    assert len(provider.interactions) == calls
