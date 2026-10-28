import pytest

from agent_autonomy_lab.entrypoints.settings import DEFAULT_LANGSMITH_PROJECT, langsmith_project


def test_default_project_name():
    assert DEFAULT_LANGSMITH_PROJECT == "agent-autonomy-lab-synthetic"


@pytest.mark.parametrize("env", [{}, {"LANGSMITH_PROJECT": ""}, {"LANGSMITH_PROJECT": "  "}])
def test_unset_project_uses_default(env):
    assert langsmith_project(env) == DEFAULT_LANGSMITH_PROJECT


def test_project_read_from_env():
    assert langsmith_project({"LANGSMITH_PROJECT": "cohort-2027-01"}) == "cohort-2027-01"


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("LANGSMITH_PROJECT", "cohort-from-env")
    assert langsmith_project() == "cohort-from-env"
