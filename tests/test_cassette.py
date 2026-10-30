import json
import os

import httpx
import litellm
import pytest

from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    CassetteMismatch,
    RecordingTransport,
    ReplayTransport,
    litellm_transport,
    replaying,
)
from agent_autonomy_lab.adapters.litellm.cassette import REPLAY_API_KEY

URL = "https://api.example.test/v1/responses"
SECRET = "sk-test-secret-value-7f3a"


def echo(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"echo": body["n"]}, headers={"x-request-id": "req_1"})


def send(transport: httpx.BaseTransport, n: int, **extra) -> httpx.Response:
    with httpx.Client(transport=transport) as client:
        return client.post(
            URL, json={"n": n, **extra}, headers={"Authorization": f"Bearer {SECRET}"}
        )


def recorded(*numbers: int) -> Cassette:
    recorder = RecordingTransport(httpx.MockTransport(echo))
    for n in numbers:
        send(recorder, n)
    return recorder.cassette({"task_id": "0"})


def test_recording_keeps_the_body_and_response_but_no_headers():
    cassette = recorded(1)
    (interaction,) = cassette.interactions
    assert (interaction.request.method, interaction.request.url) == ("POST", URL)
    assert interaction.request.body == {"n": 1}
    assert interaction.response.status == 200
    assert json.loads(interaction.response.body) == {"echo": 1}
    text = cassette.to_json()
    assert SECRET not in text
    assert "req_1" not in text


def test_the_recording_run_gets_the_provider_response_unchanged():
    recorder = RecordingTransport(httpx.MockTransport(echo))
    response = send(recorder, 7)
    assert response.json() == {"echo": 7}
    assert response.headers["x-request-id"] == "req_1"


def test_replay_serves_the_recording_in_order():
    replay = ReplayTransport(recorded(1, 2))
    assert send(replay, 1).json() == {"echo": 1}
    assert send(replay, 2).json() == {"echo": 2}
    replay.check_complete()


def test_a_request_that_differs_fails_and_so_does_every_later_one():
    replay = ReplayTransport(recorded(1, 2))
    with pytest.raises(CassetteMismatch, match=r"request 1 differs .* body\.n"):
        send(replay, 9)
    with pytest.raises(CassetteMismatch):
        send(replay, 1)  # matches the recording, but the replay has already failed
    with pytest.raises(CassetteMismatch):
        replay.check_complete()
    assert replay.served == 0


def test_an_added_field_is_named_in_the_mismatch():
    replay = ReplayTransport(recorded(1))
    with pytest.raises(CassetteMismatch, match=r"body\.reasoning"):
        send(replay, 1, reasoning={"effort": "high"})


def test_a_request_past_the_end_fails():
    replay = ReplayTransport(recorded(1))
    send(replay, 1)
    with pytest.raises(CassetteMismatch, match="request 2 has no recording"):
        send(replay, 2)


def test_unserved_responses_fail_the_check():
    replay = ReplayTransport(recorded(1, 2))
    send(replay, 1)
    with pytest.raises(CassetteMismatch, match="1 of 2 recorded responses unserved"):
        replay.check_complete()


def test_a_cassette_round_trips_and_is_never_overwritten(tmp_path):
    cassette = recorded(1, 2)
    path = tmp_path / "c.cassette.json"
    digest = cassette.save(path)
    assert len(digest) == 64
    assert Cassette.load(path) == cassette
    with pytest.raises(FileExistsError):
        cassette.save(path)


def test_only_lab_cassettes_load():
    with pytest.raises(ValueError, match="not a lab-cassette/1 cassette"):
        Cassette.from_json(json.dumps({"format": "vcr", "interactions": []}))


def test_the_transport_is_installed_for_litellm_and_then_restored():
    before = getattr(litellm, "sync_transport", None), litellm.client_session
    transport = httpx.MockTransport(echo)
    with litellm_transport(transport):
        assert litellm.sync_transport is transport
    assert (getattr(litellm, "sync_transport", None), litellm.client_session) == before


# LiteLLM's service logger asks for an event loop on a failed chat completion.
@pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning")
@pytest.mark.parametrize(
    "model",
    [
        "openai/responses/gpt-6-luna",  # the baseline's agent and user (Responses bridge)
        "openai/responses/gpt-5.4-mini-2026-03-17",  # τ-bench's NL-assertion judge
        "gpt-4.1",  # OpenAI chat completions, through the OpenAI SDK
        "claude-opus-4-5",  # τ-bench's review judges (Anthropic)
    ],
)
def test_every_provider_route_reaches_the_transport(model):
    seen = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "offline"}})

    with litellm_transport(httpx.MockTransport(refuse)), pytest.raises(Exception):  # noqa: B017
        litellm.completion(
            model=model, messages=[{"role": "user", "content": "hi"}], api_key=SECRET
        )
    assert len(seen) == 1


def test_a_replay_hides_provider_keys_and_restores_them(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with replaying(recorded(1)):
        assert os.environ["OPENAI_API_KEY"] == REPLAY_API_KEY
        assert os.environ["ANTHROPIC_API_KEY"] == REPLAY_API_KEY
    assert os.environ["OPENAI_API_KEY"] == SECRET
    assert "ANTHROPIC_API_KEY" not in os.environ
