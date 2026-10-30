"""Record, replay with tracing off, replay with tracing on, and compare: offline.

Runs real τ-bench retail task 0 through the entrypoints, with a scripted
provider behind the recording transport instead of a model.
"""

import json
from uuid import uuid4

import pytest
from tau2.agent.llm_agent import create_llm_agent
from tau2.registry import registry

from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    RecordingTransport,
    provider_of,
    replaying,
)
from agent_autonomy_lab.adapters.tau2 import live_run
from agent_autonomy_lab.adapters.tau2.live_run import LiveRunSettings, run_untraced_task
from agent_autonomy_lab.adapters.tau2.traced_agent import UntracedAgentFactory, live_toolkit
from agent_autonomy_lab.domain.equivalence import COMPARED_FIELDS
from agent_autonomy_lab.entrypoints import (
    cassette_run,
    compare_outcomes,
    run_options,
    trace_live_run,
)
from tests.fake_provider import ScriptedProvider, task_0_outputs

CASSETTE = "cassettes/task-0.cassette.json"


def unique(name: str) -> str:
    """τ-bench's registry refuses a name twice per process."""
    return f"{name}_{uuid4().hex[:8]}"


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """A data directory, a scripted provider, and fresh agent names for every run."""
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-key")
    monkeypatch.chdir(tmp_path)
    provider = ScriptedProvider(task_0_outputs())
    monkeypatch.setattr(cassette_run, "RecordingTransport", lambda: RecordingTransport(provider))
    monkeypatch.setattr(
        cassette_run,
        "register_untraced_agent",
        lambda: live_run.register_untraced_agent(unique("test_untraced")),
    )
    monkeypatch.setattr(
        trace_live_run,
        "register_traced_agent",
        lambda: live_run.register_traced_agent(unique("test_traced")),
    )
    return tmp_path


def outcome(lab, save_to: str) -> dict:
    path = lab / "simulations" / save_to / "outcome.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_tracing_changes_nothing_on_a_replay(lab, capsys):
    assert cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"]) == 0
    assert cassette_run.main(["replay", "--cassette", CASSETTE, "--save-to", "off"]) == 0
    args = ["--replay", CASSETTE, "--save-to", "on", "--dry-run"]
    assert trace_live_run.main(args) == 0
    capsys.readouterr()

    runs = [str(lab / "simulations" / name) for name in ("off", "on", "rec")]
    assert compare_outcomes.main(runs) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["equivalent"] is True
    assert [c["differing"] for c in report["comparisons"]] == [[], []]
    assert set(report["comparisons"][0]["fields"]) == set(COMPARED_FIELDS)

    on = outcome(lab, "on")
    assert on["tracing"] == "on" and on["mode"] == "replay"
    assert on["outcome"]["tool_calls"] == [
        {
            "name": "find_user_id_by_name_zip",
            "arguments": {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"},
        }
    ]
    assert outcome(lab, "off")["cassette"]["served"] == on["cassette"]["served"] == 4
    assert outcome(lab, "rec")["cassette"]["sha256"] == outcome(lab, "off")["cassette"]["sha256"]

    summary = json.loads((lab / "simulations" / "on" / "summary.json").read_text())
    assert summary["fingerprint"]["match"] is True
    assert summary["thread_id"] == on["thread_id"]


def test_the_compared_db_hash_is_the_live_database_after_the_run(lab):
    """The scripted conversation only reads, so the live database ends as it began."""
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    rec = outcome(lab, "rec")["outcome"]
    assert rec["final_db_hash"]
    assert rec["reward_breakdown"] == {"DB": 0.0, "NL_ASSERTION": 1.0}  # no exchange was made


def test_an_agent_that_sends_anything_else_fails_the_replay(lab):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    cassette = Cassette.load(lab / CASSETTE)
    settings = LiveRunSettings(**cassette.settings)

    name = unique("test_changed_prompt")
    changed = UntracedAgentFactory(name)

    def build(tools, domain_policy, **kwargs):
        changed.toolkits.append(live_toolkit(tools))
        return create_llm_agent(tools, domain_policy + "\nBe brief.", **kwargs)

    registry.register_agent_factory(build, name)
    with replaying(cassette) as transport, pytest.raises(Exception):  # noqa: B017
        run_untraced_task(settings, changed)
    assert "body.instructions" in str(transport.failure)


def test_a_replay_refuses_settings_the_cassette_was_not_recorded_with(lab, capsys):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    capsys.readouterr()
    args = ["replay", "--cassette", CASSETTE, "--save-to", "off", "--seed", "7"]
    assert cassette_run.main(args) == 1
    assert "differ from the cassette's in: seed" in capsys.readouterr().err
    args = ["--replay", CASSETTE, "--save-to", "on", "--dry-run", "--task-id", "1"]
    assert trace_live_run.main(args) == 1
    assert "differ from the cassette's in: task_id" in capsys.readouterr().err
    assert not (lab / "simulations" / "off").exists()


def test_a_cassette_is_never_overwritten(lab, capsys):
    (lab / "cassettes").mkdir()
    (lab / CASSETTE).write_text("{}")
    assert cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"]) == 1
    assert "never overwritten" in capsys.readouterr().err


def test_differing_outcomes_are_reported_by_field(lab, capsys):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    path = lab / "simulations" / "rec" / "outcome.json"
    data = json.loads(path.read_text())
    data["outcome"]["final_db_hash"] = "0" * 64
    data["outcome"]["tool_calls"][0]["arguments"]["zip"] = "00000"
    changed = lab / "changed.json"
    changed.write_text(json.dumps(data))
    capsys.readouterr()
    assert compare_outcomes.main([str(path), str(changed)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["equivalent"] is False
    assert report["comparisons"][0]["differing"] == ["tool_calls", "final_db_hash"]


def test_tools_that_do_not_reveal_one_toolkit_give_no_database():
    class NotAToolkit:
        def read(self):
            return None

    class FakeTool:
        def __init__(self, func):
            self._func = func

    assert live_toolkit([]) is None
    assert live_toolkit([FakeTool(NotAToolkit().read)]) is None
    assert live_toolkit([FakeTool(len)]) is None


def test_a_replay_needs_a_cassette(lab, capsys):
    assert cassette_run.main(["replay", "--cassette", "missing.json", "--save-to", "off"]) == 1
    assert "not a cassette file" in capsys.readouterr().err


def test_a_value_of_another_json_type_is_a_difference(lab, capsys):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    path = lab / "simulations" / "rec" / "outcome.json"
    data = json.loads(path.read_text())
    data["outcome"]["transcript"][0]["error"] = 0  # recorded as false
    changed = lab / "changed.json"
    changed.write_text(json.dumps(data))
    capsys.readouterr()
    assert compare_outcomes.main([str(path), str(changed)]) == 1
    assert json.loads(capsys.readouterr().out)["comparisons"][0]["differing"] == ["transcript"]


def test_settings_of_another_json_type_are_refused(lab, capsys):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    capsys.readouterr()
    args = ["--agent-llm-args", '{"reasoning_effort": "medium", "parallel_tool_calls": true}']
    path = lab / CASSETTE
    data = json.loads(path.read_text())
    data["settings"]["agent_llm_args"]["parallel_tool_calls"] = 1
    path.unlink()
    path.write_text(json.dumps(data))
    assert cassette_run.main(["replay", "--cassette", CASSETTE, "--save-to", "off", *args]) == 1
    assert "differ from the cassette's in: agent_llm_args" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--agent-llm-args", "--user-llm-args"])
@pytest.mark.parametrize(
    "refused",
    [
        {"api_key": "sk-x"},
        {"extra_headers": {"Authorization": "Bearer sk-x"}},
        {"headers": {"x-api-key": "sk-x"}},
        {"api_base": "https://proxy.example.test/?key=sk-x"},
    ],
)
def test_only_accepted_model_arguments_reach_a_cassette(lab, capsys, option, refused):
    args = ["record", "--cassette", CASSETTE, "--save-to", "rec", option, json.dumps(refused)]
    with pytest.raises(SystemExit) as stop:
        cassette_run.main(args)
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert f"not an accepted model argument: {next(iter(refused))}" in err
    assert "credentials come from the environment" in err
    assert not (lab / CASSETTE).exists()


def test_every_accepted_model_argument_parses():
    accepted = {name: 1 for name in run_options.MODEL_ARGS}
    args = run_options._model_args(json.dumps(accepted))
    assert args == accepted


@pytest.mark.parametrize("option", ["--agent-llm", "--user-llm"])
@pytest.mark.parametrize("model", ["gemini/gemini-2.0-flash", "azure/gpt-4.1"])
def test_a_cassette_run_refuses_providers_its_transports_do_not_cover(lab, capsys, option, model):
    assert (
        cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec", option, model])
        == 1
    )
    assert "a cassette run needs models from openai, anthropic" in capsys.readouterr().err
    assert not (lab / CASSETTE).exists()
    assert not (lab / "simulations" / "rec").exists()


def test_a_traced_replay_refuses_uncovered_providers(lab, capsys):
    cassette_run.main(["record", "--cassette", CASSETTE, "--save-to", "rec"])
    capsys.readouterr()
    args = ["--replay", CASSETTE, "--save-to", "on", "--dry-run", "--user-llm", "gemini/x"]
    assert trace_live_run.main(args) == 1
    assert "refused: user_llm 'gemini/x' (gemini)" in capsys.readouterr().err


def test_covered_providers_are_the_tested_routes():
    assert provider_of("openai/responses/gpt-6-luna") == "openai"
    assert provider_of("claude-opus-4-5") == "anthropic"
    assert provider_of("gemini/gemini-2.0-flash") == "gemini"
