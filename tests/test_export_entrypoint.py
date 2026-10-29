import pytest

from agent_autonomy_lab.entrypoints.export_simulation import main
from tests.conftest import SAMPLE_RUN, SAMPLE_SIMULATION_ID

ARGS = ["--source-tz", "America/Denver", "--experiment-class", "official_upstream_baseline"]


def test_dry_run_builds_and_checks_without_sending(capsys, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main([str(SAMPLE_RUN), *ARGS, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run, nothing sent" in out
    assert SAMPLE_SIMULATION_ID in out


def test_export_without_an_api_key_stops(capsys, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main([str(SAMPLE_RUN), *ARGS]) == 1
    assert "LANGSMITH_API_KEY is not set" in capsys.readouterr().err


def test_missing_run_is_reported(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    assert main(["no-such-run", *ARGS, "--dry-run"]) == 1
    assert "no saved run found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad", [["--source-tz", "Mars/Olympus"], ["--experiment-class", "made_up"]]
)
def test_invalid_options_are_rejected(bad):
    args = dict(zip(ARGS[::2], ARGS[1::2], strict=True)) | dict([bad])
    with pytest.raises(SystemExit):
        main([str(SAMPLE_RUN), *[part for item in args.items() for part in item], "--dry-run"])
