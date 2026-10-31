import json
from datetime import UTC, datetime

from agent_autonomy_lab.adapters.langsmith import shape_errors
from agent_autonomy_lab.domain.events import EventType
from agent_autonomy_lab.entrypoints.thread_probe import (
    CHILD_MODEL_MARKER,
    CHILD_TOOL_MARKER,
    ROOT_MARKER,
    main,
    probe_events,
)

START = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def test_the_probe_passes_the_sink_checks():
    assert shape_errors(probe_events("thread-probe-test", START), []) == []


def test_each_marker_is_only_where_it_tests():
    root, *children = probe_events("thread-probe-test", START)
    root_content = json.dumps([root.to_dict()["inputs"], root.to_dict()["outputs"]])
    child_content = json.dumps([child.to_dict()["outputs"] for child in children])

    assert root.event_type is EventType.CONVERSATION
    assert ROOT_MARKER in root_content
    assert CHILD_TOOL_MARKER not in root_content
    assert CHILD_MODEL_MARKER not in root_content
    assert ROOT_MARKER not in child_content
    assert CHILD_TOOL_MARKER in child_content
    assert CHILD_MODEL_MARKER in child_content


def test_dry_run_sends_nothing(capsys, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main(["--dry-run"]) == 0
    assert "dry run, nothing sent" in capsys.readouterr().out


def test_a_real_send_needs_an_api_key(capsys, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main([]) == 1
    assert "LANGSMITH_API_KEY is not set" in capsys.readouterr().err
