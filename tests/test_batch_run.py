"""The batch runner's rules, on scripted attempts and on one offline run of real task 0."""

import itertools
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import litellm
import pytest
import yaml

from agent_autonomy_lab.adapters.litellm import (
    RecordingTransport,
    effective_settings,
    provider_calls,
)
from agent_autonomy_lab.adapters.litellm.cassette import (
    Interaction,
    RecordedRequest,
    RecordedResponse,
)
from agent_autonomy_lab.adapters.tau2 import live_run
from agent_autonomy_lab.application.batch_report import batch_figures, simulation_record
from agent_autonomy_lab.application.batch_run import result_feedback, run_batch
from agent_autonomy_lab.application.check_results import CheckResults
from agent_autonomy_lab.domain.agent_turns import UnfinishedSimulation
from agent_autonomy_lab.domain.batch import (
    BatchPlan,
    ModelPrice,
    ProviderCall,
    SimulationAttempt,
    SpendLedger,
    TaskList,
)
from agent_autonomy_lab.domain.evaluation import EvaluatorOutput, FailureKind, ResultStatus
from agent_autonomy_lab.domain.events import ExperimentClass
from agent_autonomy_lab.domain.reference_writes import ReferenceWrites, WriteCall
from agent_autonomy_lab.domain.simulations import Reward, SavedSimulation, ToolCall
from agent_autonomy_lab.entrypoints import run_batch as entrypoint
from agent_autonomy_lab.ports.batch import (
    ScoredSimulation,
    ScriptedSimulationRunner,
    ScriptedSimulationScorer,
)
from agent_autonomy_lab.ports.model_client import ScriptedModelClient
from agent_autonomy_lab.ports.reference_writes import CannedReferenceWrites
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink
from tests.fake_provider import ScriptedProvider, task_0_outputs
from tests.test_trace_live_run import SENT, T0, recording, transcript

MODEL = "gpt-6-luna"
JUDGE = "gpt-5.4-mini-2026-03-17"
PRICES = {MODEL: ModelPrice(0.10, 0.50), JUDGE: ModelPrice(0.75, 4.50)}
TASKS = TaskList("list-v1", "baseline", ("0", "4"), "abc")


def plan(trials=1, cap=5.0, retries=1, tasks=TASKS) -> BatchPlan:
    return BatchPlan(tasks, trials, cap, retries)


def _simulation(sim_id, termination="user_stop", reward=None, task_id="0", messages=None):
    return SavedSimulation(
        sim_id, task_id, T0, 8.5, termination, messages or transcript(), reward=reward
    )


def finished(task_id="0", trial=0, attempt=0, termination="user_stop", usd=0.0, **fields):
    """An attempt that finished; ``usd`` is its spend, as output tokens at $0.50 per 1M."""
    sim_id = fields.pop("sim_id", f"sim-{uuid4().hex[:8]}")
    calls = (ProviderCall(MODEL, 200, 0, round(usd * 2_000_000)),) if usd else ()
    defaults = {
        "trial_seed": 670487,
        "reference": SENT,
        "policy_version": "policy-1",
        "agent_name": "lab_traced_llm_agent",
        "recording": recording(),
        "simulation": _simulation(
            sim_id, termination, task_id=task_id, reward=fields.pop("reward", None)
        ),
        "provider_calls": calls,
    }
    return SimulationAttempt(task_id, trial, attempt, **{**defaults, **fields})


def raised(task_id="0", trial=0, attempt=0) -> SimulationAttempt:
    return SimulationAttempt(
        task_id,
        trial,
        attempt,
        trial_seed=670487,
        reference=SENT,
        policy_version="policy-1",
        agent_name="lab_traced_llm_agent",
        recording=None,
        unfinished=UnfinishedSimulation(
            f"unfinished-{uuid4().hex[:8]}", task_id, "ProviderError", "503"
        ),
    )


def _one_result(subject):
    """A scorer's output that passes when the record has spans, as a real check would."""
    results = CheckResults("task_success", "v1", subject)
    results.passed(None, "Good.", [event.event_id for event in subject.events[:1]])
    return EvaluatorOutput(results=tuple(results.results))


def scorer(usd=0.0, fail=None):
    def score(subject):
        if fail:
            raise fail
        calls = (ProviderCall(MODEL, 200, 0, round(usd * 2_000_000)),) if usd else ()
        return ScoredSimulation(_one_result(subject), calls)

    return ScriptedSimulationScorer(score)


def execute(attempts, batch=None, score=None, sink=None):
    runner = ScriptedSimulationRunner(attempts)
    score = score or scorer()
    outcome = run_batch(
        batch or plan(),
        runner,
        score,
        sink or InMemoryTraceSink(),
        "openai/responses/gpt-6-luna",
        PRICES,
    )
    return outcome, runner, score


def test_the_plan_runs_whole_trials_first_so_an_early_stop_is_even():
    assert plan(trials=2).units() == (("0", 0), ("4", 0), ("0", 1), ("4", 1))


@pytest.mark.parametrize(
    "bad",
    [{"trials": 0}, {"cap_usd": 0.0}, {"max_retries": -1}],
)
def test_a_plan_refuses_limits_that_mean_nothing(bad):
    with pytest.raises(ValueError):
        BatchPlan(**{"task_list": TASKS, "trials": 1, "cap_usd": 1.0, **bad})


def test_a_task_list_refuses_a_repeated_task():
    with pytest.raises(ValueError, match="twice"):
        TaskList("v", "s", ("0", "0"), "abc")


def test_every_attempt_runs_is_scored_and_is_traced_under_the_lab_label():
    sink = InMemoryTraceSink()
    attempts = [finished("0"), finished("4")]
    outcome, runner, score = execute(attempts, sink=sink)

    assert runner.asked == [("0", 0, 0), ("4", 0, 0)]
    assert outcome.stop_reason is None and outcome.not_run == []
    assert len(score.subjects) == 2 and all(row.results for row in outcome.rows)
    assert all(row.trace is not None for row in outcome.rows)
    assert {event.context.experiment_class for event in sink.events} == {
        ExperimentClass.CUSTOM_LAB_EXPERIMENT
    }


def test_the_results_cite_the_spans_that_were_sent():
    sink = InMemoryTraceSink()
    outcome, _, _ = execute([finished("0"), finished("4")], sink=sink)
    sent = {event.event_id for event in sink.events}
    assert sent >= {i for row in outcome.rows for r in row.results for i in r.evidence_ids}


def test_the_cap_stops_the_run_and_records_what_did_not_run():
    outcome, runner, _ = execute(
        [finished("0", usd=0.60)],
        batch=plan(trials=2, cap=0.50),
    )

    assert runner.asked == [("0", 0, 0)]
    assert "$0.5 cap" in outcome.stop_reason
    assert outcome.not_run == [
        {"task_id": "4", "trial": 0, "reason": outcome.stop_reason},
        {"task_id": "0", "trial": 1, "reason": outcome.stop_reason},
        {"task_id": "4", "trial": 1, "reason": outcome.stop_reason},
    ]


def test_a_run_that_reaches_the_cap_is_kept_and_stays_unscored():
    outcome, _, score = execute([finished("0", usd=0.60)], batch=plan(cap=0.50))

    (row,) = outcome.rows
    assert score.subjects == []
    assert row.results == () and "cap" in row.not_scored
    assert row.trace is not None


def test_judge_spend_counts_toward_the_cap():
    outcome, runner, _ = execute(
        [finished("0", usd=0.30)],
        batch=plan(cap=0.50),
        score=scorer(usd=0.30),
    )
    assert runner.asked == [("0", 0, 0)]
    assert "cap" in outcome.stop_reason


def test_an_application_failure_stays_scored_and_is_not_retried():
    outcome, runner, _ = execute(
        [finished("0", termination="max_steps")],
        batch=plan(tasks=TaskList("v", "s", ("0",), "abc")),
    )
    (row,) = outcome.rows
    assert runner.asked == [("0", 0, 0)]
    assert row.standing.run_class.value == "application_failure"
    assert row.results and outcome.retries == []


def test_an_invalid_run_is_retried_kept_and_logged_apart():
    tasks = TaskList("v", "s", ("0",), "abc")
    outcome, runner, _ = execute([raised(), finished(attempt=1)], batch=plan(tasks=tasks))

    assert runner.asked == [("0", 0, 0), ("0", 0, 1)]
    assert [row.is_invalid for row in outcome.rows] == [True, False]
    (retry,) = outcome.retries
    assert retry["because"] == "raised_exception" and retry["cause"] == "ProviderError"

    figures = batch_figures(outcome, CannedReferenceWrites(), JUDGE)
    assert figures["runs"]["invalid"] == {
        "count": 1,
        "reasons": {"raised_exception": 1},
        "causes": {"ProviderError": 1},
    }
    assert figures["runs"]["runner_retries"] == outcome.retries
    assert figures["runs"]["completed"] == 1


def test_an_invalid_run_leaves_the_denominators(monkeypatch):
    from agent_autonomy_lab.application import evaluate_simulation as evaluate
    from agent_autonomy_lab.domain.evaluation import EvaluationSubject

    class Evaluator:
        evaluation_key = "task_success"
        version = "v1"

        def evaluate(self, subject: EvaluationSubject):
            results = CheckResults(self.evaluation_key, self.version, subject)
            results.failed(
                None, "Reward 0.", [e.event_id for e in subject.events], FailureKind.OUTCOME
            )
            return results.output()

    def score(subject):
        return ScoredSimulation(evaluate.evaluate_simulation(subject, (Evaluator(),)))

    outcome, _, _ = execute(
        [finished(termination="infrastructure_error"), finished(attempt=1)],
        batch=plan(tasks=TaskList("v", "s", ("0",), "abc")),
        score=ScriptedSimulationScorer(score),
    )
    metric = batch_figures(outcome, CannedReferenceWrites(), JUDGE)["metrics"]["task_success"]
    assert (metric["denominator"], metric["invalid"]) == (1, 1)
    assert metric["invalid_reasons"] == {"infrastructure_error": 1}


def test_retries_stop_at_the_limit_and_the_last_invalid_run_counts():
    tasks = TaskList("v", "s", ("0",), "abc")
    outcome, runner, _ = execute([raised(), raised(attempt=1)], batch=plan(tasks=tasks))
    assert len(runner.asked) == 2
    assert len(outcome.rows) == 2
    assert len(outcome.retries) == 1
    assert outcome.stop_reason is None


def test_a_fingerprint_mismatch_stops_the_run_before_scoring():
    changed = replace(SENT, prompt_version="prompt-dddddddddddd")
    outcome, runner, score = execute([finished("0", reference=changed)], batch=plan())

    (row,) = outcome.rows
    assert score.subjects == []
    assert "upstream's llm_agent" in outcome.stop_reason
    assert row.not_scored == outcome.stop_reason
    assert row.attempt.fingerprint_differences["2"] == {"prompt_version": SENT.prompt_version}
    assert outcome.not_run == [{"task_id": "4", "trial": 0, "reason": outcome.stop_reason}]
    assert runner.asked == [("0", 0, 0)]


@pytest.mark.parametrize(
    "call",
    [ProviderCall(MODEL, 200, None, None), ProviderCall("another-model", 200, 1, 1)],
)
def test_a_call_that_cannot_be_priced_stops_the_run(call):
    outcome, runner, _ = execute([finished("0", provider_calls=(call,))])
    assert "could not be priced" in outcome.stop_reason
    assert runner.asked == [("0", 0, 0)]


def test_a_refused_request_costs_nothing_and_does_not_stop_the_run():
    ledger = SpendLedger(PRICES)
    ledger.add([ProviderCall(MODEL, 429), ProviderCall("unlisted", 500)])
    assert ledger.total_usd == 0.0 and ledger.unpriced_calls == 0
    assert ledger.by_model()[MODEL]["failed_calls"] == 1


def test_list_price_comes_from_usage():
    ledger = SpendLedger(PRICES)
    ledger.add([ProviderCall(MODEL, 200, 1_000_000, 1_000_000), ProviderCall(JUDGE, 200, 0, 1000)])
    assert ledger.total_usd == pytest.approx(0.60 + 0.0045)


def test_a_runner_that_raises_stops_the_run_with_the_trial_not_run():
    outcome, _, _ = execute([RuntimeError("broken")])
    assert outcome.stop_reason == "The runner raised RuntimeError: broken"
    assert [item["task_id"] for item in outcome.not_run] == ["0", "4"]


def test_a_scorer_that_raises_stops_the_run_and_keeps_the_simulation():
    outcome, _, _ = execute([finished("0")], score=scorer(fail=RuntimeError("broken")))
    (row,) = outcome.rows
    assert "scorer raised" in row.not_scored


def test_a_trace_backend_failure_is_reported_and_does_not_stop_the_run():
    class Refusing(InMemoryTraceSink):
        def flush(self):
            raise RuntimeError("503")

    outcome, _, _ = execute([finished("0"), finished("4")], sink=Refusing())
    assert outcome.stop_reason is None and len(outcome.rows) == 2
    figures = batch_figures(outcome, CannedReferenceWrites(), JUDGE)
    assert figures["traces"]["sent"] == 0
    assert figures["traces"]["not_sent"][0]["reason"] == "RuntimeError: 503"


def test_a_run_that_raised_before_a_conversation_is_still_scored_and_has_no_trace():
    outcome, _, score = execute(
        [raised(), raised(attempt=1), finished("4")],
    )
    assert score.subjects[0].raised_exception and score.subjects[0].stop_cause == "ProviderError"
    assert outcome.rows[0].trace is None
    assert "no conversation" in outcome.rows[0].trace_error


def _write_call() -> ToolCall:
    return ToolCall("call-w", "cancel_pending_order", {"order_id": "#W1", "reason": "x"})


def _with_write(content="{}") -> tuple:
    messages = list(transcript())
    messages[2] = replace(messages[2], tool_calls=(_write_call(),))
    messages[3] = replace(
        messages[3],
        content=content,
        tool_call_id="call-w",
        execution_status="executed",
    )
    return tuple(messages)


def test_results_become_feedback_on_their_tool_span_or_else_the_root():
    events = live_events()
    tool = next(event for event in events if event.tool_name)

    class Subject:
        simulation_id = "sim"
        scenario_id = "0"

    results = CheckResults("order_status", "v1", Subject)
    results.passed(str(tool.run_id), "Pending.", [tool.event_id])
    results.unknown(None, "No span.")
    results._add("elsewhere", ResultStatus.NOT_APPLICABLE, "No target.")

    written, on_root = result_feedback(results.results, events)

    assert written.run_id == tool.run_id
    assert written.comment == "PASS: Pending." and written.score == 1.0
    assert written.value["evaluator_version"] == "v1"
    assert on_root.run_id == events[0].run_id
    assert on_root.score is None and on_root.value["status"] == "INSUFFICIENT_EVIDENCE"


def live_events():
    from agent_autonomy_lab.application.trace_live_run import live_trace

    simulation = _simulation("sim")
    return tuple(live_trace(recording(), simulation)[0])


def test_scores_reach_the_sink_with_the_trace():
    sink = InMemoryTraceSink()
    execute([finished("0"), finished("4")], sink=sink)
    assert sink.link_errors() == []
    assert sum(item.key == "task_success" for item in sink.feedback) == 2


def _reference() -> CannedReferenceWrites:
    write = WriteCall("cancel_pending_order", {"order_id": "#W1", "reason": "x"})
    return CannedReferenceWrites({"0": ReferenceWrites("0", (write,))})


def _other_write_run(db_match: bool) -> SimulationAttempt:
    messages = _with_write()
    other = ToolCall("call-w", "cancel_pending_order", {"order_id": "#W2", "reason": "x"})
    messages = (*messages[:2], replace(messages[2], tool_calls=(other,)), *messages[3:])
    reward = Reward(
        1.0 if db_match else 0.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": db_match}}
    )
    sim = _simulation("sim-other", reward=reward, messages=messages)
    return finished("0", sim_id="sim-other", simulation=sim)


def test_a_correct_end_state_through_other_writes_is_reported():
    outcome, _, _ = execute(
        [_other_write_run(True)], batch=plan(tasks=TaskList("v", "s", ("0",), "a"))
    )
    question = batch_figures(outcome, _reference(), JUDGE)["tool_correctness_question"]
    assert question["correct_end_states_compared"] == 1
    assert question["reached_through_different_writes"] == 1
    assert question["simulations"][0]["simulation_id"] == "sim-other"


def test_a_wrong_end_state_through_other_writes_answers_nothing():
    outcome, _, _ = execute(
        [_other_write_run(False)], batch=plan(tasks=TaskList("v", "s", ("0",), "a"))
    )
    question = batch_figures(outcome, _reference(), JUDGE)["tool_correctness_question"]
    assert question["correct_end_states_compared"] == 0
    assert question["reached_through_different_writes"] == 0


def test_the_same_writes_are_not_reported_as_different():
    reward = Reward(1.0, {"DB": 1.0}, ("DB",), {"db_check": {"db_match": True}})
    sim = _simulation("sim-same", reward=reward, messages=_with_write())
    attempt = finished("0", sim_id="sim-same", simulation=sim)
    outcome, _, _ = execute([attempt], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    question = batch_figures(outcome, _reference(), JUDGE)["tool_correctness_question"]
    assert question["correct_end_states_compared"] == 1
    assert question["reached_through_different_writes"] == 0


def _nl_reward() -> Reward:
    return Reward(1.0, {"NL_ASSERTION": 1.0}, ("NL_ASSERTION",), {"nl_assertions": [{"met": True}]})


def test_hook_c_agrees_when_the_callback_saw_the_judge():
    calls = (ProviderCall(JUDGE, 200, 100, 20),)
    seen = ({"model": JUDGE, "cost": 0.0002, "prompt": 100, "completion": 20},)
    attempt = finished("0", reward=_nl_reward(), provider_calls=calls, callback_calls=seen)
    outcome, _, _ = execute([attempt], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    check = batch_figures(outcome, CannedReferenceWrites(), JUDGE)["hook_c"]
    assert (check["expected_calls"], check["callback_calls"], check["provider_calls"]) == (1, 1, 1)
    assert check["prompt_tokens"] == 100 and check["litellm_cost_usd"] == 0.0002
    assert check["mismatched_simulations"] == []


def test_hook_c_failing_silently_is_reported():
    calls = (ProviderCall(JUDGE, 200, 100, 20),)
    attempt = finished("0", reward=_nl_reward(), provider_calls=calls, callback_calls=())
    outcome, _, _ = execute([attempt], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    check = batch_figures(outcome, CannedReferenceWrites(), JUDGE)["hook_c"]
    assert check["callback_calls"] == 0 and check["expected_calls"] == 1
    (mismatch,) = check["mismatched_simulations"]
    assert mismatch["task_id"] == "0"
    assert mismatch["all_calls"]["provider_by_model"] == {JUDGE: 1}
    assert mismatch["all_calls"]["callback_by_model"] == {}


def test_a_component_that_checked_nothing_stays_out_of_its_denominator():
    checked = Reward(
        0.0,
        {"DB": 1.0, "NL_ASSERTION": 0.0},
        ("DB", "NL_ASSERTION"),
        {
            "db_check": {"db_match": True},
            "nl_assertions": [{"met": False}],
        },
    )
    unchecked = Reward(
        1.0,
        {"DB": 1.0, "NL_ASSERTION": 1.0},
        ("DB", "NL_ASSERTION"),
        {
            "db_check": {"db_match": True},
        },
    )
    outcome, _, _ = execute([finished("0", reward=checked), finished("4", reward=unchecked)])
    assert batch_figures(outcome, CannedReferenceWrites(), JUDGE)["task_success_components"] == {
        "DB": {"numerator": 2, "denominator": 2},
        "NL_ASSERTION": {"numerator": 0, "denominator": 1},
    }


def test_a_record_holds_no_conversation():
    outcome, _, _ = execute([finished("0")], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    text = json.dumps(simulation_record(outcome.rows[0], CannedReferenceWrites(), JUDGE))
    assert "Exchange my keyboard" not in text
    assert "Good." in text


def test_operational_figures_keep_their_denominators():
    outcome, _, _ = execute([finished("0")], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    figures = batch_figures(outcome, CannedReferenceWrites(), JUDGE)["operational"]
    assert figures["failed_tools"] == {"numerator": 0, "denominator": 1}
    assert figures["turns"] == {"numerator": 6, "denominator": 1}
    assert figures["latency_seconds"]["agent_turn"] == {"n": 1, "median": 1.5, "p95": 1.5}


def _interaction(body, response, status=200):
    return Interaction(
        RecordedRequest("POST", "https://x", body),
        RecordedResponse(status, "application/json", json.dumps(response)),
    )


def test_provider_calls_read_each_route_s_usage():
    calls = provider_calls(
        [
            _interaction({"model": "a"}, {"usage": {"input_tokens": 3, "output_tokens": 4}}),
            _interaction({"model": "b"}, {"usage": {"prompt_tokens": 5, "completion_tokens": 6}}),
            _interaction({"model": "c"}, {"error": "busy"}, status=429),
        ]
    )
    assert calls == (
        ProviderCall("a", 200, 3, 4),
        ProviderCall("b", 200, 5, 6),
        ProviderCall("c", 429, None, None),
    )
    assert calls[2].is_unpriced_usage_gap is False


def test_a_successful_response_with_no_usage_cannot_be_priced():
    (call,) = provider_calls([_interaction({"model": "a"}, {"output": []})])
    assert call.is_unpriced_usage_gap is True


def test_effective_settings_show_what_was_sent_by_role_and_what_the_model_reported():
    settings = effective_settings(
        [
            _interaction(
                {"model": MODEL, "input": "secret text", "instructions": "secret"},
                {"reasoning": {"effort": "medium"}},
            ),
            _interaction(
                {"model": MODEL, "input": "x", "tools": ["t"], "tool_choice": "auto"},
                {"reasoning": {"effort": "medium"}},
            ),
            _interaction({"model": JUDGE, "messages": ["x"], "temperature": 0}, {}),
        ],
        JUDGE,
    )
    assert settings == {
        "user": {"sent": {}, "reported_effort": "medium"},
        "agent": {"sent": {"tool_choice": "auto"}, "reported_effort": "medium"},
        "benchmark_judge": {"sent": {"temperature": 0}, "reported_effort": None},
    }


def test_the_task_list_comes_from_the_versioned_file():
    tasks = entrypoint.read_task_list(entrypoint.DEFAULT_TASK_LIST, "baseline")
    assert tasks.version == "m4-task-sets-v1" and len(tasks.task_ids) == 20
    assert len(tasks.sha256) == 64
    assert entrypoint.read_task_list(entrypoint.DEFAULT_TASK_LIST, "baseline", ["4"]).task_ids == (
        "4",
    )


def test_a_smoke_cannot_name_a_task_outside_the_set():
    with pytest.raises(ValueError, match="not in m4-task-sets-v1"):
        entrypoint.read_task_list(entrypoint.DEFAULT_TASK_LIST, "baseline", ["1"])


def test_the_budget_scales_one_simulation_to_sixty():
    outcome, _, _ = execute(
        [finished("0", usd=0.015)], batch=plan(tasks=TaskList("v", "s", ("0",), "a"))
    )
    budget = entrypoint._budget(outcome)
    assert budget["projected_60_simulations_usd"] == pytest.approx(0.90)
    assert budget["within_expected_total"] is True


def test_the_exit_code_flags_what_a_reader_must_look_at():
    outcome, _, _ = execute([finished("0")], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    figures = batch_figures(outcome, CannedReferenceWrites(), JUDGE)
    assert entrypoint._exit_code(outcome, figures) == 0
    assert entrypoint._exit_code(outcome, {**figures, "traces": {"not_sent": [{}]}}) == 1
    outcome.stop_reason = "stopped"
    assert entrypoint._exit_code(outcome, figures) == 1


def _verdict() -> str:
    return json.dumps(
        {"score": 2, "evidence": "Quoted.", "uncertain": False, "no_transfer_needed": True}
    )


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Real task 0 through hook A with a scripted provider and scripted judges."""
    monkeypatch.setenv("TAU2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-key")
    provider = ScriptedProvider(task_0_outputs())
    monkeypatch.setattr(entrypoint, "RecordingTransport", lambda: RecordingTransport(provider))
    monkeypatch.setattr(
        entrypoint,
        "register_traced_agent",
        lambda: live_run.register_traced_agent(f"test_batch_{uuid4().hex[:8]}"),
    )
    monkeypatch.setattr(
        entrypoint, "LiteLLMModelClient", lambda: ScriptedModelClient(itertools.repeat(_verdict()))
    )
    yield tmp_path
    litellm.logging_callback_manager._reset_all_callbacks()


def _main(tmp_path, *extra) -> int:
    return entrypoint.main(
        [
            "--only-task",
            "0",
            "--trials",
            "1",
            "--save-to",
            "smoke",
            "--report-dir",
            str(tmp_path / "report"),
            "--dry-run",
            *extra,
        ]
    )


def test_an_offline_run_saves_every_artifact_and_a_template_manifest(offline, capsys):
    assert _main(offline) == 0

    saved = offline / "simulations" / "smoke" / "task-0-trial-0-attempt-0"
    for name in (
        "simulation.json",
        "run.cassette.json",
        "judges.cassette.json",
        "trace_events.jsonl",
        "raw_outputs.jsonl",
        "attempt.json",
    ):
        assert (saved / name).is_file(), name

    data = yaml.safe_load((offline / "report" / "manifest.yaml").read_text())
    template = (
        "experiment_id started_at_utc traffic_type experiment_class tau_bench_commit "
        "tau_bench_task_split selected_task_ids scenario_manifest_version model_id "
        "user_model_id judge_model declared_settings effective_settings agent_version "
        "prompt_version policy_version seed trials_per_task concurrency attempts completed "
        "failed_to_run langsmith_project report_path notes"
    )
    assert set(template.split()) <= set(data)
    assert data["experiment_class"] == "custom_lab_experiment"
    assert data["lab_commit"] != "unknown"
    assert data["scenario_manifest_version"] == "m4-task-sets-v1"
    assert data["fingerprint_mismatched_attempts"] == 0
    assert data["completed"] == 1 and data["failed_to_run"] == 0
    assert data["cost"]["by_model"]["gpt-6-luna"]["calls"] == 4
    assert data["cost"]["billed_usd"] is None
    assert data["hook_c"]["all_calls_callback"] == data["hook_c"]["all_calls_provider_successes"]
    assert data["effective_settings"]["judges"] == [
        {"max_tokens": 2048, "output_config": {"effort": "low"}}
    ]
    assert data["declared_settings"]["agent"] == {"reasoning_effort": "medium"}
    assert data["effective_settings"]["agent"][0]["sent"].get("reasoning_effort") is None


def test_every_evaluator_scores_the_offline_run_and_cites_the_trace(offline, capsys):
    assert _main(offline) == 0

    (record,) = [
        json.loads(line)
        for line in (offline / "report" / "simulations.jsonl").read_text().splitlines()
    ]
    keys = {result["evaluation_key"] for result in record["results"]}
    assert {
        "authentication_order",
        "user_access",
        "message_form",
        "order_status",
        "expected_writes",
        "task_success",
        "required_customer_confirmation",
        "clarity_customer_effort",
        "conversation_efficiency",
        "escalation_correctness",
        "unauthorized_attempts",
        "unauthorized_executions",
        "autonomy_level",
    } <= keys

    saved = offline / "simulations" / "smoke" / "task-0-trial-0-attempt-0" / "trace_events.jsonl"
    sent = {json.loads(line)["event_id"] for line in saved.read_text().splitlines()}
    cited = {event_id for result in record["results"] for event_id in result["evidence_ids"]}
    assert cited and cited <= sent
    errors = [r for r in record["results"] if r["status"] == "EVALUATOR_ERROR"]
    assert errors == []
    authentication = next(
        r for r in record["results"] if r["evaluation_key"] == "authentication_order"
    )
    assert authentication["status"] != "INSUFFICIENT_EVIDENCE"


def test_each_raw_output_id_a_result_gives_can_be_followed(offline, capsys):
    assert _main(offline) == 0

    (record,) = [
        json.loads(line)
        for line in (offline / "report" / "simulations.jsonl").read_text().splitlines()
    ]
    saved = offline / "simulations" / "smoke" / "task-0-trial-0-attempt-0" / "raw_outputs.jsonl"
    raw = {
        json.loads(line)["output_id"]: json.loads(line) for line in saved.read_text().splitlines()
    }
    linked = [r for r in record["results"] if r["raw_output_id"]]
    assert linked and all(r["raw_output_id"] in raw for r in linked)
    assert "call" in raw[linked[0]["raw_output_id"]]["payload"]


def test_a_run_never_overwrites_a_saved_one(offline, capsys):
    (offline / "simulations" / "smoke").mkdir(parents=True)
    assert _main(offline) == 1
    assert "never overwritten" in capsys.readouterr().err


def test_a_live_run_needs_a_trace_key(monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert entrypoint.main(["--trials", "1", "--save-to", "x", "--report-dir", "y"]) == 1
    assert "LANGSMITH_API_KEY" in capsys.readouterr().err


def test_a_raised_provider_error_in_the_real_runner_is_an_invalid_attempt(offline, monkeypatch):
    class Unreachable(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("no route")

    monkeypatch.setattr(entrypoint, "RecordingTransport", lambda: RecordingTransport(Unreachable()))
    code = _main(offline, "--max-retries", "0")

    data = yaml.safe_load((offline / "report" / "manifest.yaml").read_text())
    assert data["completed"] == 0
    assert data["failed_to_run"] == 1
    assert sum(data["runs"]["invalid"]["reasons"].values()) == 1
    assert code == 1  # Nothing could be traced, so a reader must look.


def test_a_call_repeating_a_failed_call_counts_as_a_retry_whatever_its_key_order():
    call = ToolCall("call-1", "get_order_details", {"order_id": "#W1", "x": 1})
    again = ToolCall("call-2", "get_order_details", {"x": 1, "order_id": "#W1"})
    base = transcript()
    messages = (
        base[0],
        base[1],
        replace(base[2], tool_calls=(call,)),
        replace(base[3], error=True, execution_status="executed"),
        replace(base[4], tool_calls=(again,), content=None),
        replace(base[3], turn_index=5, tool_call_id="call-2", execution_status="executed"),
    )
    attempt = finished("0", simulation=_simulation("sim-r", messages=messages), sim_id="sim-r")
    outcome, _, _ = execute([attempt], batch=plan(tasks=TaskList("v", "s", ("0",), "a")))
    figures = batch_figures(outcome, CannedReferenceWrites(), JUDGE)["operational"]
    assert figures["tool_retries"] == {"numerator": 1, "denominator": 1}
    assert figures["failed_tools"] == {"numerator": 1, "denominator": 2}
