import json
import os
from pathlib import Path

import httpx
import litellm
import pytest
import yaml

from agent_autonomy_lab.adapters.litellm import (
    Cassette,
    CassetteMismatch,
    RequestMatchedReplay,
    replaying_by_request,
)
from agent_autonomy_lab.adapters.litellm.cassette import (
    REPLAY_API_KEY,
    Interaction,
    RecordedRequest,
    RecordedResponse,
)
from agent_autonomy_lab.application.scorecard import (
    checks_table,
    comparable,
    compare_results,
    component_counts,
    evidence_coverage,
    judges_table,
    metrics_table,
    outcome_conduct_table,
    replace_blocks,
    rollup_counts,
    status_counts,
)
from agent_autonomy_lab.domain.events import TraceEvent
from agent_autonomy_lab.entrypoints import scorecard

REPO = Path(__file__).parent.parent
REPORTS = REPO / "reports/m4-baseline"


def _rows():
    lines = (REPORTS / "simulations.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _manifest():
    return yaml.safe_load((REPORTS / "manifest.yaml").read_text("utf-8"))


def _result(key="message_form", status="PASS", **fields):
    return {
        "evaluation_key": key,
        "status": status,
        "score": fields.pop("score", 1.0),
        "evidence_ids": fields.pop("evidence_ids", ["a"]),
        "reason": fields.pop("reason", "because"),
        "raw_output_id": fields.pop("raw_output_id", None),
        **fields,
    }


def test_a_new_raw_output_id_is_not_a_difference_but_losing_one_is():
    assert comparable(_result(raw_output_id="x")) == comparable(_result(raw_output_id="y"))
    assert comparable(_result(raw_output_id="x")) != comparable(_result())


def test_comparison_separates_deterministic_and_judged_and_reports_differences():
    recorded = [_result(), _result("clarity_customer_effort")]
    report = compare_results(recorded, recorded, "here")
    assert report["deterministic"] == {"compared": 1, "identical": 1}
    assert report["judged"] == {"compared": 1, "identical": 1}
    assert report["differences"] == []
    changed = [_result(score=0.0), _result("clarity_customer_effort", reason="other")]
    report = compare_results(recorded, changed, "here")
    assert report["deterministic"]["identical"] == report["judged"]["identical"] == 0
    assert len(report["differences"]) == 2


def test_a_lost_result_is_a_difference():
    report = compare_results([_result(), _result()], [_result()], "here")
    assert report["differences"] == ["here: 2 recorded, 1 replayed"]


def test_evidence_coverage_counts_a_cited_id_missing_from_the_trace():
    results = [_result(evidence_ids=["a", "b"]), _result("x", "NOT_APPLICABLE", evidence_ids=[])]
    assert evidence_coverage(results, ["a"]) == {
        "decided": 1,
        "decided_citing_evidence": 1,
        "ids_cited": 2,
        "ids_found": 1,
    }


def test_counts_equal_the_manifest_written_by_the_run():
    rows, manifest = _rows(), _manifest()
    for key, metric in manifest["metrics"].items():
        counts = status_counts(rows, key)
        assert {name: metric[name] for name in counts} == counts, key
    for name, roll_up in manifest["simulation_roll_ups"].items():
        assert rollup_counts(rows, name) == roll_up, name


def test_every_metric_of_the_run_appears_on_the_scorecard():
    rows, manifest = _rows(), _manifest()
    shown = {
        *scorecard_checks(checks_table(rows)),
        "task_success",
        "expected_writes",
        "unauthorized_attempts",
        "unauthorized_executions",
        "clarity_customer_effort",
        "conversation_efficiency",
        "escalation_correctness",
        "required_customer_confirmation",
    }
    assert shown == set(manifest["metrics"])


def scorecard_checks(table):
    return [line.split("`")[1] for line in table.splitlines()[2:]]


def test_each_metric_row_shows_its_denominator_and_non_scored_counts():
    rows = _rows()
    tables = (metrics_table(rows), checks_table(rows), judges_table(rows, rows))
    for table in tables:
        assert "Numerator / denominator" in table
        for name in ("Not applicable", "Insufficient", "Error", "Invalid"):
            assert name in table
    assert "overall" not in "".join(tables).lower()


def test_outcome_and_conduct_table_shows_the_disagreements():
    table = outcome_conduct_table(_rows())
    assert "| Reward 1 | 42 | 3 | 1 |" in table
    assert "| Reward 0 | 9 | 5 | 0 |" in table


def test_a_part_a_status_the_table_does_not_show_is_refused():
    row = {"benchmark": {"reward": 1.0}, "roll_ups": {"policy_compliance": "INVALID"}}
    with pytest.raises(ValueError, match="does not show"):
        outcome_conduct_table([row])


def test_replace_blocks_requires_each_marker_once():
    text = "a\n<!-- generated:x -->\nold\n<!-- /generated:x -->\nb"
    assert "new" in replace_blocks(text, {"x": "new"})
    assert "old" not in replace_blocks(text, {"x": "new"})
    with pytest.raises(ValueError, match="generated block named y"):
        replace_blocks(text, {"y": "new"})


def test_committed_scorecard_matches_its_sources(monkeypatch):
    monkeypatch.chdir(REPO)
    assert scorecard.main(["render", "--check"]) == 0


def test_committed_rescore_reproduced_everything():
    check = yaml.safe_load((REPORTS / "rescore-check.yaml").read_text("utf-8"))
    assert check["reproduced"] is True
    assert check["baseline"]["differences"] == []
    assert check["baseline"]["requests_with_no_recording_in"] == ["task 43 trial 0"]
    evidence = check["baseline"]["evidence"]
    assert evidence["decided"] == evidence["decided_citing_evidence"]
    assert evidence["ids_cited"] == evidence["ids_found"]


def test_scorecard_states_its_limits():
    text = (REPORTS / "scorecard.md").read_text("utf-8")
    for phrase in (
        "not safety assurance",
        "Sample size",
        "Synthetic traffic",
        "One model in both roles",
    ):
        assert phrase in text


def test_trace_event_round_trips_through_its_record():
    line = (REPO / "tests/fixtures/saved-trace-event.json").read_text("utf-8")
    data = json.loads(line)
    assert TraceEvent.from_dict(data).to_dict() == data


def _cassette(*bodies):
    return Cassette(
        tuple(
            Interaction(
                RecordedRequest("POST", "https://x.test/v1", body),
                RecordedResponse(200, "application/json", json.dumps({"served": index})),
            )
            for index, body in enumerate(bodies)
        )
    )


def _post(transport, body):
    request = httpx.Request("POST", "https://x.test/v1", json=body)
    return transport.handle_request(request)


def test_matched_replay_serves_by_request_whatever_the_order():
    transport = RequestMatchedReplay(_cassette({"a": 1}, {"b": 2}))
    assert _post(transport, {"b": 2}).json() == {"served": 1}
    assert _post(transport, {"a": 1}).json() == {"served": 0}
    assert (transport.served, transport.unmatched, transport.unserved) == (2, 0, 0)


def test_matched_replay_serves_identical_requests_in_recorded_order():
    transport = RequestMatchedReplay(_cassette({"a": 1}, {"a": 1}))
    assert _post(transport, {"a": 1}).json() == {"served": 0}
    assert _post(transport, {"a": 1}).json() == {"served": 1}
    with pytest.raises(CassetteMismatch):
        _post(transport, {"a": 1})


def test_matched_replay_fails_one_unknown_request_and_keeps_serving():
    transport = RequestMatchedReplay(_cassette({"a": 1}))
    with pytest.raises(CassetteMismatch):
        _post(transport, {"never": "recorded"})
    assert _post(transport, {"a": 1}).json() == {"served": 0}
    assert transport.unmatched == 1


def test_matched_replay_reports_recordings_left_unserved():
    transport = RequestMatchedReplay(_cassette({"a": 1}, {"b": 2}))
    _post(transport, {"a": 1})
    assert transport.unserved == 1


def test_replaying_by_request_replaces_provider_keys_and_restores_them(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real")
    previous = litellm.sync_transport if hasattr(litellm, "sync_transport") else None
    with replaying_by_request(_cassette({"a": 1})) as transport:
        assert os.environ["ANTHROPIC_API_KEY"] == REPLAY_API_KEY
        assert litellm.sync_transport is transport
    assert os.environ["ANTHROPIC_API_KEY"] == "real"
    assert getattr(litellm, "sync_transport", None) is previous


def test_rescore_needs_the_data_directory(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("TAU2_DATA_DIR", raising=False)
    assert scorecard.main(["rescore", str(tmp_path)]) == 1
    assert "TAU2_DATA_DIR" in capsys.readouterr().err


def test_bundle_has_a_fixed_hash_and_lists_every_file(monkeypatch, tmp_path):
    source = tmp_path / "source"
    for name in ("m4-baseline/a.json", "m4-adjudication/b.json"):
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text("{}", encoding="utf-8")
    (source / "m4-baseline/.DS_Store").write_text("x", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    monkeypatch.setattr(scorecard, "REPORTS", tmp_path / "reports")
    hashes = []
    for name in ("one.tar.gz", "two.tar.gz"):
        assert scorecard.main(["bundle", str(source), "--out", str(tmp_path / name)]) == 0
        record = yaml.safe_load((tmp_path / "reports/bundle-manifest.yaml").read_text("utf-8"))
        hashes.append(record["archive_sha256"])
    assert hashes[0] == hashes[1]
    assert [item["path"] for item in record["files"]] == [
        "m4-adjudication/b.json",
        "m4-baseline/a.json",
    ]
    with pytest.raises(SystemExit, match="never overwritten"):
        scorecard.main(["bundle", str(source), "--out", str(tmp_path / "one.tar.gz")])


def test_hash_check_finds_a_changed_missing_or_extra_file(tmp_path):
    for name in ("m4-baseline/a", "m4-baseline/b", "m4-adjudication/c"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("new", encoding="utf-8")
    manifest = {
        "files": [
            {"path": "m4-baseline/a", "sha256": "wrong"},
            {"path": "m4-baseline/gone", "sha256": "x"},
            {
                "path": "m4-adjudication/c",
                "sha256": scorecard.file_sha256(tmp_path / "m4-adjudication/c"),
            },
        ]
    }
    found = scorecard._check_hashes(tmp_path, manifest)
    assert found == {
        "files": 3,
        "missing": ["m4-baseline/gone"],
        "extra": ["m4-baseline/b"],
        "changed": ["m4-baseline/a"],
    }


def _clean_check():
    part = {"decided": 1, "decided_citing_evidence": 1, "ids_cited": 1, "ids_found": 1}
    return {
        "bundle_files": {"missing": [], "extra": [], "changed": []},
        "baseline": {
            "differences": [],
            "recordings_unserved": 0,
            "evidence": dict(part),
            "roll_ups": {"compared": 1, "identical": 1},
        },
        "rubric_v2": {
            "differences": [],
            "recordings_unserved": 0,
            "requests_with_no_recording": 0,
            "evidence_in_rebuilt_trace": dict(part),
        },
        "repeats": {},
    }


def test_a_clean_check_passes():
    assert scorecard._passed(_clean_check())


@pytest.mark.parametrize(
    "break_it",
    [
        lambda c: c["bundle_files"]["changed"].append("x"),
        lambda c: c["bundle_files"]["extra"].append("x"),
        lambda c: c["baseline"]["differences"].append("x"),
        lambda c: c["baseline"]["roll_ups"].update(identical=0),
        lambda c: c["baseline"]["evidence"].update(ids_found=0),
        lambda c: c["baseline"]["evidence"].update(decided_citing_evidence=0),
        lambda c: c["rubric_v2"]["evidence_in_rebuilt_trace"].update(ids_found=0),
        lambda c: c["rubric_v2"].update(requests_with_no_recording=1),
        lambda c: c["baseline"].update(recordings_unserved=1),
    ],
)
def test_any_gap_fails_the_check(break_it):
    check = _clean_check()
    break_it(check)
    assert not scorecard._passed(check)


def test_rescore_stops_when_the_bundle_lacks_a_file(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    for name in ("m4-baseline", "m4-adjudication"):
        (tmp_path / name).mkdir()
    out = tmp_path / "check.yaml"
    assert scorecard.main(["rescore", str(tmp_path), "--out", str(out)]) == 1
    assert "lacks" in capsys.readouterr().err
    assert not out.exists()


def test_evidence_refuses_an_unknown_simulation(monkeypatch, tmp_path):
    monkeypatch.chdir(REPO)
    with pytest.raises(SystemExit, match="no task nope"):
        scorecard.main(["evidence", str(tmp_path), "--task", "nope", "--trial", "0"])


def test_reward_components_count_unchecked_tasks_as_not_applicable():
    components = component_counts(_rows())
    for name, part in _manifest()["task_success_components"].items():
        assert {k: components[name][k] for k in part} == part
    assert components["NL_ASSERTION"]["not_applicable"] == 33
    assert components["DB"]["not_applicable"] == 0
    assert "n/a" not in metrics_table(_rows())


def test_an_invalid_run_leaves_the_component_denominator():
    row = {
        "run_class": "invalid",
        "benchmark": {"db_match": None, "reward_breakdown": {"DB": 0.0}},
    }
    assert component_counts([row])["DB"]["invalid"] == 1
    assert component_counts([row])["DB"]["denominator"] == 0


def test_bundle_refuses_an_empty_source_and_keeps_the_manifest(monkeypatch, tmp_path):
    (tmp_path / "reports").mkdir()
    manifest = tmp_path / "reports/bundle-manifest.yaml"
    manifest.write_text("kept", encoding="utf-8")
    monkeypatch.setattr(scorecard, "REPORTS", tmp_path / "reports")
    (tmp_path / "source/m4-baseline").mkdir(parents=True)
    (tmp_path / "source/m4-baseline/a.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="m4-adjudication"):
        scorecard.main(["bundle", str(tmp_path / "source"), "--out", str(tmp_path / "x.tar.gz")])
    assert manifest.read_text(encoding="utf-8") == "kept"
    assert not (tmp_path / "x.tar.gz").exists()
