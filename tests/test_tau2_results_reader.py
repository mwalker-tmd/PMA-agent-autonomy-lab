from datetime import UTC, datetime

import pytest

from agent_autonomy_lab.adapters.tau2 import read_results, results_path
from agent_autonomy_lab.domain.simulations import ExecutionStatus, Role
from tests.conftest import DENVER, NOT_EXPORTED, SAMPLE_RUN, SAMPLE_SIMULATION_ID


@pytest.fixture
def run():
    return read_results(SAMPLE_RUN, DENVER)


def test_run_provenance_is_read(run):
    assert run.run_name == "sample-run"
    assert run.agent_implementation == "llm_agent"
    assert run.model_id == run.user_model_id == "openai/responses/gpt-6-luna"
    assert run.policy_text.startswith("# Retail agent policy")


def test_simulation_fields_are_read_with_utc_times(run):
    (simulation,) = run.simulations
    assert simulation.simulation_id == SAMPLE_SIMULATION_ID
    assert simulation.task_id == "0"
    assert simulation.start_time == datetime(2026, 10, 27, 6, 25, 9, tzinfo=UTC)
    assert simulation.duration_seconds == 8.5
    assert simulation.termination_reason == "user_stop"
    assert [m.turn_index for m in simulation.messages] == [0, 1, 2, 3, 4, 5]
    assert all(m.timestamp.utcoffset().total_seconds() == 0 for m in simulation.messages)


def test_messages_keep_usage_tool_calls_and_results(run):
    opener, ask, call, result, reply, _ = run.simulations[0].messages
    assert (opener.tokens, opener.cost, opener.is_model_output) == (None, None, False)
    assert ask.is_model_output and ask.tokens.prompt_tokens == 700
    assert call.generation_seconds == 1.5
    (tool_call,) = call.tool_calls
    assert (tool_call.call_id, tool_call.name) == ("call_1", "get_order_details")
    assert tool_call.arguments == {"order_id": "#W0000001"}
    assert result.role is Role.TOOL and result.tool_call_id == "call_1"
    assert result.error and result.execution_status is ExecutionStatus.EXECUTED
    assert reply.content.startswith("I could not find")


def test_reward_and_its_checks_are_read(run):
    reward = run.simulations[0].reward
    assert reward.reward == 0.0
    assert reward.breakdown == {"DB": 1.0, "NL_ASSERTION": 0.0}
    assert reward.basis == ("DB", "NL_ASSERTION")
    assert set(reward.details) == {"db_check", "nl_assertions", "action_checks", "env_assertions"}


def test_raw_data_and_hidden_instructions_are_not_read(run):
    assert not any(marker in repr(run) for marker in NOT_EXPORTED)
    assert not any(marker in repr(run.simulations[0].messages) for marker in NOT_EXPORTED)


def test_results_path_accepts_a_file_a_directory_or_a_save_name(tmp_path):
    saved = tmp_path / "simulations" / "sample-run"
    saved.mkdir(parents=True)
    (saved / "results.json").write_text(SAMPLE_RUN.read_text())
    assert results_path(str(SAMPLE_RUN)) == SAMPLE_RUN
    assert results_path(str(SAMPLE_RUN.parent)) == SAMPLE_RUN
    assert results_path("sample-run", str(tmp_path)) == saved / "results.json"


def test_unknown_save_name_is_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        results_path("no-such-run", str(tmp_path))


def test_results_path_without_a_data_dir_fails_clearly(monkeypatch):
    monkeypatch.delenv("TAU2_DATA_DIR", raising=False)
    with pytest.raises(FileNotFoundError, match="TAU2_DATA_DIR is not set"):
        results_path("no-such-run")
