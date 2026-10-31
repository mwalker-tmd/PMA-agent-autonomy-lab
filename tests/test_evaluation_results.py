import json
from uuid import uuid4

import pytest

from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluatorOutput,
    FailureKind,
    RawEvaluatorOutput,
    ResultStatus,
    judge_output,
)

KEY = "required_customer_confirmation"
STATUSES = {
    "PASS",
    "FAIL",
    "NOT_APPLICABLE",
    "INSUFFICIENT_EVIDENCE",
    "EVALUATOR_ERROR",
    "INVALID",
}


def make_result(status=ResultStatus.PASS, **overrides):
    fields = {
        "evaluation_key": KEY,
        "status": status,
        "reason": "A reason.",
        "evaluator_version": "confirmation-v1",
        "simulation_id": "sim-1",
        "evidence_ids": (uuid4(),),
    }
    if status is ResultStatus.FAIL:
        fields["failure_kind"] = FailureKind.VIOLATION
    if status is ResultStatus.INVALID:
        fields["invalid_reason"] = "infrastructure_error"
    return EvaluationResult(**(fields | overrides))


def test_statuses_are_exactly_the_contracts():
    assert {status.value for status in ResultStatus} == STATUSES


def test_to_dict_holds_every_field_of_the_required_format_as_json():
    event_id = uuid4()
    result = make_result(
        ResultStatus.FAIL,
        score=0,
        evidence_ids=(event_id,),
        scenario_id="cancellation_ambiguous_01",
        target="write-span",
    )
    record = json.loads(json.dumps(result.to_dict()))
    assert record == {
        "evaluation_key": KEY,
        "status": "FAIL",
        "score": 0,
        "evidence_ids": [str(event_id)],
        "reason": "A reason.",
        "evaluator_version": "confirmation-v1",
        "scenario_id": "cancellation_ambiguous_01",
        "simulation_id": "sim-1",
        "target": "write-span",
        "failure_kind": "violation",
        "invalid_reason": None,
        "raw_output_id": None,
    }


def test_status_is_coerced_from_its_value():
    assert make_result("PASS").status is ResultStatus.PASS


@pytest.mark.parametrize("status", [ResultStatus.PASS, ResultStatus.FAIL])
def test_a_scored_result_needs_evidence(status):
    with pytest.raises(ValueError, match="needs evidence_ids"):
        make_result(status, evidence_ids=())


@pytest.mark.parametrize(
    "status",
    [
        ResultStatus.NOT_APPLICABLE,
        ResultStatus.INSUFFICIENT_EVIDENCE,
        ResultStatus.EVALUATOR_ERROR,
        ResultStatus.INVALID,
    ],
)
def test_an_unscored_status_can_have_no_evidence_but_never_a_score(status):
    assert make_result(status, evidence_ids=()).score is None
    with pytest.raises(ValueError, match="has no score"):
        make_result(status, score=2)


@pytest.mark.parametrize("evidence", [("event-100",), "event-100", (1,)])
def test_evidence_must_be_trace_event_ids(evidence):
    with pytest.raises((ValueError, TypeError)):
        make_result(evidence_ids=evidence)


@pytest.mark.parametrize("score", [True, "2", float("nan"), float("inf")])
def test_a_score_must_be_a_finite_number(score):
    with pytest.raises(ValueError, match="score must be"):
        make_result(score=score)


def test_a_fail_states_its_kind_and_only_a_fail_has_one():
    with pytest.raises(ValueError, match="failure_kind"):
        make_result(ResultStatus.FAIL, failure_kind=None)
    with pytest.raises(ValueError, match="failure_kind"):
        make_result(failure_kind=FailureKind.OUTCOME)


def test_an_invalid_result_states_its_reason_and_only_it_has_one():
    with pytest.raises(ValueError, match="invalid_reason"):
        make_result(ResultStatus.INVALID, invalid_reason=None)
    with pytest.raises(ValueError, match="invalid_reason"):
        make_result(invalid_reason="user_error")


@pytest.mark.parametrize("name", ["evaluation_key", "reason", "evaluator_version", "simulation_id"])
def test_identifying_fields_are_required(name):
    with pytest.raises(ValueError, match=name):
        make_result(**{name: ""})


def test_evidence_given_as_a_generator_is_kept():
    event_id = uuid4()
    assert make_result(evidence_ids=(e for e in [event_id])).evidence_ids == (event_id,)


def test_an_output_rejects_a_link_to_a_raw_output_it_does_not_hold():
    with pytest.raises(ValueError, match="not in raw_outputs"):
        EvaluatorOutput(results=(make_result(raw_output_id=uuid4()),))


@pytest.mark.parametrize(
    "owner",
    [
        (KEY, "confirmation-v1", "sim-2"),
        (KEY, "confirmation-v2", "sim-1"),
        ("another_metric", "confirmation-v1", "sim-1"),
    ],
)
def test_an_output_rejects_a_link_to_another_results_raw_output(owner):
    raw = RawEvaluatorOutput(uuid4(), *owner, {"judge": "reply"})
    with pytest.raises(ValueError, match="belongs to another result"):
        EvaluatorOutput(results=(make_result(raw_output_id=raw.output_id),), raw_outputs=(raw,))


def test_an_output_rejects_two_raw_outputs_with_one_id():
    output_id = uuid4()
    first = RawEvaluatorOutput(output_id, KEY, "confirmation-v1", "sim-1", {"judge": "one"})
    second = RawEvaluatorOutput(output_id, KEY, "confirmation-v1", "sim-1", {"judge": "two"})
    with pytest.raises(ValueError, match="distinct output_id"):
        EvaluatorOutput(results=(), raw_outputs=(first, second))


def test_an_output_holds_only_result_records():
    with pytest.raises(ValueError, match="EvaluationResult"):
        EvaluatorOutput(results=(object(),))
    with pytest.raises(ValueError, match="RawEvaluatorOutput"):
        EvaluatorOutput(results=(), raw_outputs=(object(),))


def judge(stored, **overrides):
    fields = {
        "evaluation_key": "clarity",
        "evaluator_version": "clarity-v1",
        "simulation_id": "sim-1",
        "failure_kind": FailureKind.OUTCOME,
        "evidence_ids": (uuid4(),),
    }
    return judge_output(stored, **(fields | overrides))


@pytest.mark.parametrize(
    "stored",
    [
        {"score": None, "comment": "400 messages: at least one message is required"},
        {"comment": "no score key"},
        {"score": None, "uncertain": True},
        {"uncertain": True},
    ],
)
def test_a_judge_failure_stored_with_no_score_is_a_row(stored):
    output = judge(stored)
    [result] = output.results
    assert result.status is ResultStatus.EVALUATOR_ERROR
    assert result.score is None
    assert output.raw_outputs[0].payload == stored
    assert result.raw_output_id == output.raw_outputs[0].output_id


@pytest.mark.parametrize(
    "stored",
    [
        {"score": 3},
        {"score": 1.5},
        {"score": True},
        {"score": "2"},
        {"score": 2, "uncertain": "no"},
        {"score": "bad", "uncertain": True},
        {"score": float("nan")},
        "not a mapping",
        None,
        object(),
    ],
)
def test_a_malformed_judge_output_is_an_error_and_is_kept(stored):
    output = judge(stored)
    assert output.results[0].status is ResultStatus.EVALUATOR_ERROR
    assert output.raw_outputs[0].payload


@pytest.mark.parametrize(
    ("score", "status", "kind"),
    [
        (2, ResultStatus.PASS, None),
        (2.0, ResultStatus.PASS, None),
        (1, ResultStatus.FAIL, FailureKind.OUTCOME),
        (0, ResultStatus.FAIL, FailureKind.OUTCOME),
    ],
)
def test_only_a_two_passes_a_rubric(score, status, kind):
    [result] = judge({"score": score, "reason": "Clear."}).results
    assert (result.status, result.failure_kind, result.reason) == (status, kind, "Clear.")
    assert result.score == int(score)


def test_an_uncertain_judge_is_insufficient_evidence_whatever_it_scored():
    [result] = judge({"score": 2, "uncertain": True}).results
    assert result.status is ResultStatus.INSUFFICIENT_EVIDENCE
    assert result.score is None


def test_raw_output_stays_out_of_the_normalized_result():
    stored = {"score": 2, "reason": "Clear.", "reasoning": "RAW-JUDGE-SENTINEL"}
    output = judge(stored)
    assert output.raw_outputs[0].payload == stored
    assert "RAW-JUDGE-SENTINEL" not in json.dumps(output.results[0].to_dict())
    assert "RAW-JUDGE-SENTINEL" not in repr(output.results[0])
    assert "RAW-JUDGE-SENTINEL" not in repr(output.raw_outputs[0])


def test_raw_output_is_a_detached_copy():
    payload = {"nested": {"value": 1}}
    raw = RawEvaluatorOutput(uuid4(), KEY, "v1", "sim-1", payload)
    payload["nested"]["value"] = 2
    assert raw.payload == {"nested": {"value": 1}}
