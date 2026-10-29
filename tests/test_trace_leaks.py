"""What reaches LangSmith: synthetic tags, no secrets, and no answer key outside feedback.

Every test sends through the real ``langsmith.Client`` with a fake HTTP
session and checks the request bodies the SDK would send, for both ways a
simulation becomes a trace: the saved-run export and the live run.
"""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from langsmith.env import get_langchain_env_var_metadata

from agent_autonomy_lab.adapters.langsmith import LangSmithTraceSink, langsmith_client
from agent_autonomy_lab.adapters.langsmith.redaction import REDACTED
from agent_autonomy_lab.adapters.tau2 import read_results
from agent_autonomy_lab.application.export_simulation import export_run
from agent_autonomy_lab.application.trace_live_run import record_live_run
from agent_autonomy_lab.domain.agent_turns import AgentFingerprint, AgentTurn, LiveRecording
from agent_autonomy_lab.domain.events import (
    EventType,
    ExperimentClass,
    Feedback,
    GateEventClass,
    new_id,
)
from agent_autonomy_lab.domain.simulations import Role
from tests.conftest import ANSWER_KEY, DENVER, NOT_EXPORTED, SAMPLE_RUN, make_event
from tests.test_langsmith_trace_sink import NOW, PROJECT, FakeSession
from tests.test_redaction import (
    FAKE_LANGSMITH_KEY,
    FAKE_OPENAI_KEY,
    FAKE_PLAIN_TOKEN,
    FAKE_QUOTED_SECRET,
)

PLANTED = (FAKE_OPENAI_KEY, FAKE_LANGSMITH_KEY, FAKE_PLAIN_TOKEN, FAKE_QUOTED_SECRET)
SENT = AgentFingerprint(
    model_id="openai/responses/gpt-6-luna",
    prompt_version="prompt-aaaaaaaaaaaa",
    tool_schema_version="tools-bbbbbbbbbbbb",
    model_args_version="args-cccccccccccc",
    model_args={"reasoning_effort": "medium", "seed": 670487},
)


class CapturingSession(FakeSession):
    """A ``FakeSession`` that also keeps the body of every request."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[str] = []

    def request(self, method, url, **kwargs):
        for name in ("data", "json"):
            if kwargs.get(name) is not None:
                body = kwargs[name]
                self.bodies.append(body.decode() if isinstance(body, bytes) else str(body))
        return super().request(method, url, **kwargs)


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


def make_sink(session: CapturingSession) -> LangSmithTraceSink:
    """A sink built as the entrypoints build it, with the redactor read from the environment."""
    client = langsmith_client(
        api_url="http://langsmith.invalid",
        api_key="fake-key",
        session=session,
        auto_batch_tracing=False,
        info={},
    )
    return LangSmithTraceSink(client, PROJECT, clock=lambda: NOW)


def sample_simulation():
    (simulation,) = read_results(SAMPLE_RUN, DENVER).simulations
    return simulation


def sample_recording(simulation, sent: AgentFingerprint = SENT) -> LiveRecording:
    """The sample simulation as the agent wrapper would have recorded it."""
    messages = simulation.messages
    turns = tuple(
        AgentTurn(
            turn_index=index,
            started_at=messages[index - 1].timestamp,
            elapsed_ms=100.0,
            fingerprint=sent,
            inputs=(messages[index - 1],),
            output=message,
        )
        for index, message in enumerate(messages)
        if message.role is Role.ASSISTANT and message.is_model_output
    )
    return LiveRecording(
        agent_version="lab_traced_llm_agent",
        started_at=simulation.start_time,
        ended_at=simulation.start_time + timedelta(seconds=simulation.duration_seconds),
        transcript=messages,
        turns=turns,
        overhead_ms=1.0,
    )


def send_export(sink):
    export_run(read_results(SAMPLE_RUN, DENVER), sink, ExperimentClass.OFFICIAL_UPSTREAM_BASELINE)


def send_live(sink):
    simulation = sample_simulation()
    record_live_run(sample_recording(simulation), simulation, sink)


PATHS = pytest.mark.parametrize("send", [send_export, send_live], ids=["export", "live"])


@PATHS
def test_every_run_and_feedback_is_tagged_synthetic(session, send):
    send(make_sink(session))
    assert session.posted and session.feedback
    for run in session.posted:
        assert run["extra"]["metadata"]["traffic_type"] == "synthetic", run["name"]
    for item in session.feedback:
        assert item["feedback_source"]["metadata"]["traffic_type"] == "synthetic", item["key"]


@PATHS
def test_answer_key_reaches_only_feedback(session, send):
    """The task's hidden scenario and answer key never reach a run the agent could see.

    NL assertions and reference actions are the answer key; they may appear
    only in feedback, the separate evaluation attachment. The user scenario
    and the evaluation criteria are not exported at all.
    """
    send(make_sink(session))
    runs = json.dumps(session.posted)
    for marker in ANSWER_KEY + NOT_EXPORTED:
        assert marker not in runs, marker
    feedback = json.dumps(session.feedback)
    for marker in NOT_EXPORTED:
        assert marker not in feedback, marker
    # The answer key does reach the evaluation attachment, so the check above is not vacuous.
    for marker in ANSWER_KEY:
        assert marker in feedback, marker


@pytest.fixture
def planted_environment(monkeypatch):
    """Fake keys in the environment, as importing τ-bench's ``load_dotenv()`` would leave them.

    ``LANGSMITH_ENDPOINT`` is copied into run metadata by the SDK itself, so
    a key embedded in it tests the redactor installed on the client.
    """
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI_KEY)
    monkeypatch.setenv("LANGSMITH_API_KEY", FAKE_LANGSMITH_KEY)
    monkeypatch.setenv("TAU2_SERVICE_TOKEN", FAKE_PLAIN_TOKEN)
    monkeypatch.setenv("TAU2_QUOTED_SECRET", FAKE_QUOTED_SECRET)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", f"https://lab:{FAKE_PLAIN_TOKEN}@langsmith.invalid")
    get_langchain_env_var_metadata.cache_clear()
    yield
    get_langchain_env_var_metadata.cache_clear()


def written_forms(secret: str) -> set[str]:
    """``secret`` as a request body could carry it: raw, or JSON-escaped once or twice."""
    forms = {secret}
    for _ in range(2):
        forms |= {json.dumps(f, ensure_ascii=a)[1:-1] for f in forms for a in (True, False)}
    return forms


def plant(message, leaked_error: str):
    """The sample message, with keys in a tool result and in a tool call's arguments.

    The arguments hold one key as a mapping key and one that JSON escapes
    (``chat_message()`` sends arguments as a JSON string).
    """
    if message.role is Role.TOOL:
        return replace(message, content=leaked_error)
    if message.tool_calls:
        (call,) = message.tool_calls
        arguments = {**call.arguments, FAKE_PLAIN_TOKEN: "failed", "note": FAKE_QUOTED_SECRET}
        return replace(message, tool_calls=(replace(call, arguments=arguments),))
    return message


def test_planted_keys_never_reach_a_payload(session, planted_environment):
    """Keys planted in content, keys, context, errors, gates, feedback, and SDK metadata."""
    simulation = sample_simulation()
    leaked_error = f"Error: upstream refused key {FAKE_OPENAI_KEY} (token {FAKE_PLAIN_TOKEN})"
    messages = tuple(plant(m, leaked_error) for m in simulation.messages)
    simulation = replace(simulation, messages=messages)
    sent = replace(
        SENT,
        model_id=f"openai/{FAKE_PLAIN_TOKEN}",
        model_args={**SENT.model_args, "api_key": FAKE_OPENAI_KEY},
    )
    recording = sample_recording(simulation, sent)

    trace = record_live_run(recording, simulation, _PlantingSink(make_sink(session)))

    payload = "\n".join(session.bodies)
    assert session.posted and session.feedback
    for key in PLANTED:
        for form in written_forms(key):
            assert form not in payload, form

    runs = {run["name"]: run for run in session.posted}
    assert runs["conversation"]["extra"]["metadata"]["LANGSMITH_ENDPOINT"] == (
        f"https://lab:{REDACTED}@langsmith.invalid"
    )
    tool = runs["get_order_details"]
    assert REDACTED in tool["error"] and REDACTED in tool["outputs"]["content"]
    assert REDACTED in tool["extra"]["metadata"]["error_message"]
    assert REDACTED in tool["events"][0]["kwargs"]["decision_reason"]
    assert tool["inputs"] == {"order_id": "#W0000001", REDACTED: "failed", "note": REDACTED}
    for item in session.feedback:
        assert item["feedback_source"]["metadata"]["model_id"] == f"openai/{REDACTED}"
    turn = next(
        r for r in session.posted if r["name"] == "model_call" and "model_args" in r["inputs"]
    )
    assert turn["inputs"]["model_args"] == {
        "reasoning_effort": "medium", "seed": 670487, "api_key": REDACTED,
    }  # fmt: skip
    planted = next(f for f in session.feedback if f["key"] == "planted")
    assert planted["comment"] == f"retried with {REDACTED}"
    assert planted["value"] == {"headers": {"x-api-key": REDACTED}}
    # What is not a secret is sent as recorded.
    assert trace.spans == len(session.posted)
    assert runs["conversation"]["extra"]["metadata"]["traffic_type"] == "synthetic"


class _PlantingSink:
    """Passes everything to ``sink``, adding a gate event and feedback that carry keys.

    Gates are emitted from Milestone 6 and the lab records no free-form
    feedback yet, so the planted ones are added here to cover those paths.
    """

    def __init__(self, sink: LangSmithTraceSink) -> None:
        self._sink = sink
        self._root = None

    def record(self, event):
        self._sink.record(event)
        if event.event_type is EventType.CONVERSATION:
            self._root = event
        if event.event_type is EventType.TOOL_CALL:
            self._sink.record(
                make_event(
                    EventType.GATE,
                    event.context,
                    run_id=event.run_id,
                    timestamp=event.timestamp,
                    tool_name=event.tool_name,
                    gate_event=GateEventClass.ATTEMPTED,
                    decision_reason=f"checked token {FAKE_PLAIN_TOKEN}",
                )
            )

    def record_feedback(self, feedback):
        self._sink.record_feedback(feedback)

    def flush(self):
        self._sink.record_feedback(
            Feedback(
                feedback_id=new_id(),
                run_id=self._root.run_id,
                context=self._root.context,
                key="planted",
                comment=f"retried with {FAKE_LANGSMITH_KEY}",
                value={"headers": {"x-api-key": FAKE_LANGSMITH_KEY}},
            )
        )
        self._sink.flush()
