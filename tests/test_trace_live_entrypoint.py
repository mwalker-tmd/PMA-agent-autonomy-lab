import json

from agent_autonomy_lab.entrypoints.trace_live_run import _parser, main


def test_defaults_match_the_milestone_2_baseline():
    # reports/m2-baseline/manifest.yaml: run.command
    args = _parser().parse_args([])
    assert args.task_id == "0"
    assert args.agent_llm == args.user_llm == "openai/responses/gpt-6-luna"
    assert args.agent_llm_args == args.user_llm_args == {"reasoning_effort": "medium"}
    assert args.seed == 42


def test_llm_args_must_be_a_json_object(capsys):
    for bad in ("not json", json.dumps(["a"])):
        try:
            _parser().parse_args(["--agent-llm-args", bad])
        except SystemExit as stop:
            assert stop.code == 2
    assert "--agent-llm-args" in capsys.readouterr().err


def test_a_live_run_needs_an_api_key_unless_dry(capsys, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main([]) == 1
    assert "LANGSMITH_API_KEY is not set" in capsys.readouterr().err


def test_a_run_never_overwrites_a_saved_one(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    (tmp_path / "simulations" / "taken").mkdir(parents=True)
    assert main(["--dry-run", "--save-to", "taken"]) == 1
    assert "already exists" in capsys.readouterr().err
