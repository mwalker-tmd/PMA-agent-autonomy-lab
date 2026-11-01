"""The τ-bench reward reader: read as given, never recomputed or merged."""

from dataclasses import replace

import pytest

from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.task_success import (
    TASK_SUCCESS_KEY,
    TaskSuccessEvaluator,
    benchmark_report,
)
from agent_autonomy_lab.domain.evaluation import EvaluationSubject, FailureKind, ResultStatus
from agent_autonomy_lab.domain.simulations import Reward
from tests.test_access_checks import SIM, TASK_0, UTC_ZONE, _events, _simulation, _steps
from tests.test_write_eligibility import _read

PASS, FAIL = ResultStatus.PASS, ResultStatus.FAIL
UNKNOWN, NOT_APPLICABLE = ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.NOT_APPLICABLE
BOTH = ("DB", "NL_ASSERTION")


def _subject(reward, termination="agent_stop", scenario="0", messages=None):
    simulation = replace(_simulation(messages or _steps(_read()), termination), reward=reward)
    return EvaluationSubject(SIM, simulation, _events(simulation), scenario_id=scenario)


def _scored(subject):
    return evaluate_simulation(subject, (TaskSuccessEvaluator(),)).results[0]


def test_a_reward_of_one_passes_and_cites_the_conversation_root():
    subject = _subject(Reward(1.0, {"DB": 1.0, "NL_ASSERTION": 1.0}, BOTH))
    result = _scored(subject)
    assert result.status is PASS
    assert result.evaluation_key == TASK_SUCCESS_KEY
    assert [e.event_type.value for e in subject.events if e.event_id in result.evidence_ids] == [
        "conversation"
    ]


@pytest.mark.parametrize("reward", [0.0, 0.5])
def test_any_other_reward_is_an_outcome_fail(reward):
    result = _scored(_subject(Reward(reward, {"DB": 1.0, "NL_ASSERTION": 0.0}, BOTH)))
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.OUTCOME


def test_a_run_with_no_reward_is_undecided():
    assert _scored(_subject(None)).status is UNKNOWN


def test_a_run_with_no_reward_is_invalid_when_an_outside_cause_stopped_it():
    assert _scored(_subject(None, "infrastructure_error")).status is ResultStatus.INVALID


def test_a_raised_run_is_invalid():
    subject = EvaluationSubject(SIM, None, (), scenario_id="0", raised_exception=True)
    assert _scored(subject).status is ResultStatus.INVALID


def test_a_task_with_no_criteria_does_not_count_as_a_pass_at_one():
    result = _scored(_subject(Reward(1.0, note="No evaluation criteria")))
    assert result.status is NOT_APPLICABLE


def test_one_with_no_basis_and_no_note_proves_nothing():
    assert _scored(_subject(Reward(1.0))).status is UNKNOWN


def test_an_early_end_is_an_outcome_fail_though_it_has_no_basis():
    note = "Simulation terminated prematurely. Termination reason: max_steps"
    result = _scored(_subject(Reward(0.0, note=note), "max_steps"))
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.OUTCOME


def test_an_early_end_from_an_outside_cause_is_invalid():
    note = "Simulation terminated prematurely. Termination reason: user_error"
    result = _scored(_subject(Reward(0.0, note=note), "user_error"))
    assert result.status is ResultStatus.INVALID


def test_a_run_with_no_task_does_not_apply():
    assert _scored(_subject(Reward(1.0, {}, BOTH), scenario=None)).status is NOT_APPLICABLE


def test_a_trace_with_no_root_has_nothing_to_cite():
    subject = replace(_subject(Reward(1.0, {}, BOTH)), events=())
    assert _scored(subject).status is UNKNOWN


# What is reported


def test_the_report_gives_each_component_separately():
    details = {
        "db_check": {"db_match": True, "db_reward": 1.0},
        "nl_assertions": [{"met": False, "nl_assertion": "x"}, {"met": True, "nl_assertion": "y"}],
        "action_checks": [
            {"action_match": True, "tool_type": "read"},
            {"action_match": False, "tool_type": "read"},
            {"action_match": False, "tool_type": "write"},
        ],
    }
    reward = Reward(0.0, {"DB": 1.0, "NL_ASSERTION": 0.0}, BOTH, details)
    report = benchmark_report(replace(_simulation(_steps(_read())), reward=reward))
    assert report == {
        "termination_reason": "agent_stop",
        "reward": 0.0,
        "reward_basis": ["DB", "NL_ASSERTION"],
        "reward_breakdown": {"DB": 1.0, "NL_ASSERTION": 0.0},
        "db_match": True,
        "nl_assertions": {"met": 1, "total": 2},
        "diagnostics": {
            "partial_action_reward": {
                "total": {"matched": 1, "of": 3},
                "read": {"matched": 1, "of": 2},
                "write": {"matched": 0, "of": 1},
            }
        },
    }


def test_the_report_of_a_run_with_no_reward_says_so():
    report = benchmark_report(_simulation(_steps(_read()), "infrastructure_error"))
    assert report == {"termination_reason": "infrastructure_error", "reward": None}


def test_the_report_holds_no_assertion_text_or_reference_action():
    details = {
        "nl_assertions": [{"met": True, "nl_assertion": "ANSWER-KEY-SENTINEL"}],
        "action_checks": [
            {"action_match": True, "tool_type": "read", "action": "ANSWER-KEY-SENTINEL"}
        ],
    }
    reward = Reward(1.0, {"DB": 1.0}, ("DB",), details)
    report = benchmark_report(replace(_simulation(_steps(_read())), reward=reward))
    assert "ANSWER-KEY-SENTINEL" not in str(report)


def test_the_reader_keeps_the_note_that_says_a_reward_is_empty():
    raw = {
        **{key: value for key, value in TASK_0.items() if key != "reward_info"},
        "reward_info": {
            "reward": 1.0,
            "reward_basis": None,
            "info": {"note": "No evaluation criteria"},
        },
    }
    simulation = simulation_from_dict(raw, UTC_ZONE)
    assert simulation.reward.note == "No evaluation criteria"
    assert simulation.reward.basis == ()


def test_the_reader_has_no_note_on_an_evaluated_reward():
    raw = {
        **{key: value for key, value in TASK_0.items() if key != "reward_info"},
        "reward_info": {"reward": 1.0, "reward_basis": ["DB"], "info": {"env": None}},
    }
    assert simulation_from_dict(raw, UTC_ZONE).reward.note is None
