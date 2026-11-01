"""The three rubric judges: what they see, how a reply maps to a result, and replay."""

import hashlib
import json
from pathlib import Path

import pytest

from agent_autonomy_lab.adapters.litellm import Cassette, replaying
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.adapters.tau2 import read_results
from agent_autonomy_lab.application.evaluate_simulation import evaluate_simulation
from agent_autonomy_lab.application.export_simulation import evidence_trace
from agent_autonomy_lab.application.rubric_judges import (
    CLARITY_KEY,
    EFFICIENCY_KEY,
    ESCALATION_KEY,
    CustomerExperienceJudge,
    EscalationJudge,
    judge_prompt,
    judge_view,
    rubric_judges,
)
from agent_autonomy_lab.application.tool_correctness import (
    TRANSFER_MESSAGE,
    TransferMessageEvaluator,
)
from agent_autonomy_lab.domain.evaluation import (
    JUDGE_MODEL,
    JUDGE_SETTINGS,
    EvaluationSubject,
    FailureKind,
    ResultStatus,
)
from agent_autonomy_lab.domain.events import ExperimentClass
from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelResponse
from agent_autonomy_lab.domain.simulations import SavedRun, ToolCall, reply_text
from agent_autonomy_lab.entrypoints.check_access import saved_subjects
from agent_autonomy_lab.ports.model_client import ScriptedModelClient
from tests.conftest import DENVER, SAMPLE_RUN
from tests.test_access_checks import (
    SIM,
    _agent,
    _events,
    _result,
    _simulation,
    _subject,
    _user,
)

FIXTURES = Path(__file__).parent / "fixtures"
TASK_0 = FIXTURES / "m2-task-0" / "simulation.json"
RECORDING = FIXTURES / "rubric-judges" / "m2-task-0.json"
PROMPT_SHA256 = {
    "clarity-v1": "b7495a1741f3074e56933d023ae42962a4803453b1682afe4f08ca5b773fd115",
    "efficiency-v1": "56ee8c16fcde35a8e480509715d98b30a9718e1d091e9ab6d4d280837cd0eae5",
    "escalation-v1": "6136890fb2315274d387f30c85953f7c2232f11c32fc01fec90ea752a945398d",
}

PASS, FAIL, ERROR = ResultStatus.PASS, ResultStatus.FAIL, ResultStatus.EVALUATOR_ERROR
UNKNOWN, NOT_APPLICABLE = ResultStatus.INSUFFICIENT_EVIDENCE, ResultStatus.NOT_APPLICABLE
_TRANSFER = "transfer_to_human_agents"


def _reply(score=2, evidence="The agent said what comes next.", uncertain=False, **extra) -> str:
    return json.dumps({"score": score, "evidence": evidence, "uncertain": uncertain, **extra})


def _conversation():
    """The fixed opener, a request, and an answer in the usual wrapper."""
    return (
        _agent(0, content="Hi! How can I help you today?"),
        _user(1, "Where is my order?"),
        _agent(2, content='{"message": "Your order has shipped."}'),
    )


def _transferred(reply: str = TRANSFER_MESSAGE, error=False):
    call = ToolCall("c1", _TRANSFER, {"summary": "Out of scope."})
    return (
        _agent(0, content="Hi! How can I help you today?"),
        _user(1, "I want to sue you."),
        _agent(2, call),
        _result(3, "c1", "Transfer successful", error=error),
        _agent(4, content=reply),
    )


def _clarity(replies) -> tuple[CustomerExperienceJudge, ScriptedModelClient]:
    client = ScriptedModelClient(replies)
    return CustomerExperienceJudge(CLARITY_KEY, "clarity-v1", client), client


def _escalation(replies) -> tuple[EscalationJudge, ScriptedModelClient]:
    client = ScriptedModelClient(replies)
    return EscalationJudge(client), client


def _only(judge, messages, termination="agent_stop"):
    output = evaluate_simulation(_subject(messages, termination), [judge])
    (result,) = output.results
    return result, output


def _only_with_events(judge, messages, events):
    output = evaluate_simulation(_subject(messages, events=events), [judge])
    (result,) = output.results
    return result, output


# A reply becomes a result.


@pytest.mark.parametrize(
    ("score", "status", "kind"),
    [(2, PASS, None), (1, FAIL, FailureKind.OUTCOME), (0, FAIL, FailureKind.OUTCOME)],
)
@pytest.mark.parametrize(
    ("key", "version"),
    [(CLARITY_KEY, "clarity-v1"), (EFFICIENCY_KEY, "efficiency-v1")],
)
def test_a_customer_experience_reply_maps_to_the_result(key, version, score, status, kind):
    client = ScriptedModelClient([_reply(score, "Quoted evidence.")])
    judge = CustomerExperienceJudge(key, version, client)
    result, output = _only(judge, _conversation())
    assert (result.status, result.score, result.failure_kind) == (status, score, kind)
    assert result.reason == "Quoted evidence."
    assert result.evaluator_version == version
    assert len(result.evidence_ids) == 1
    assert result.raw_output_id == output.raw_outputs[0].output_id
    assert output.raw_outputs[0].payload["score"] == score


def test_each_result_records_the_prompt_version_model_and_settings_sent():
    sent = {"max_tokens": 2048, "output_config": {"effort": "low"}}
    reported = ModelResponse(_reply(), "the-model-it-reports", 100, 20, 0.5, "end_turn", sent)
    judge, client = _clarity([reported])
    result, output = _only(judge, _conversation())
    assert result.judge_run.to_dict() == {
        "prompt_version": "clarity-v1",
        "model": "the-model-it-reports",
        "settings_sent": sent,
    }
    assert result.to_dict()["judge_run"] == result.judge_run.to_dict()
    assert client.requests[0].model == JUDGE_MODEL
    assert client.requests[0].settings == JUDGE_SETTINGS
    call = output.raw_outputs[0].payload["call"]
    assert (call["input_tokens"], call["output_tokens"], call["cost_usd"]) == (100, 20, 0.5)


def test_the_run_is_recorded_on_a_result_the_run_standing_changes():
    judge, _ = _clarity([_reply()])
    result, _ = _only(judge, _conversation(), "infrastructure_error")
    assert result.status is ResultStatus.INVALID
    assert result.judge_run.prompt_version == "clarity-v1"


# A reply that cannot be used.


@pytest.mark.parametrize(
    "text",
    [
        "",
        "It looks clear to me.",
        '```json\n{"score": 2}\n```',
        '["score", 2]',
        '{"score": 2, "evidence": "Cut o',
        json.dumps({"score": 2, "uncertain": False}),
        _reply(evidence=""),
        _reply(evidence=7),
        json.dumps({"score": 2, "evidence": "No flag."}),
        _reply(uncertain="no"),
        _reply(score=3),
        _reply(score="2"),
        _reply(score=True),
        _reply(score=None),
    ],
)
@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_a_malformed_reply_is_an_evaluator_error_and_is_kept(make, text):
    judge, _ = make([text])
    result, output = _only(judge, _conversation())
    assert result.status is ERROR
    assert result.score is None
    assert result.judge_run.prompt_version == judge.version
    assert output.raw_outputs[0].payload


@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_a_failed_model_call_is_an_evaluator_error_with_nothing_claimed_as_sent(make):
    judge, _ = make([ModelCallError("BadRequestError: no")])
    result, output = _only(judge, _conversation())
    assert result.status is ERROR
    assert result.reason == "The judge's reply failed: the model call failed."
    assert result.judge_run.model == JUDGE_MODEL
    assert result.judge_run.settings_sent is None
    assert output.raw_outputs[0].payload["error_message"] == "BadRequestError: no"


# The uncertainty flag and the score.


@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_an_uncertain_reply_with_a_score_is_insufficient_evidence(make):
    judge, _ = make([_reply(1, uncertain=True)])
    result, _ = _only(judge, _conversation())
    assert (result.status, result.score) == (UNKNOWN, None)


@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_an_uncertain_reply_with_no_score_is_an_evaluator_error(make):
    judge, _ = make([json.dumps({"evidence": "Unsure.", "uncertain": True})])
    result, _ = _only(judge, _conversation())
    assert result.status is ERROR


# What does not reach a model.


@pytest.mark.parametrize(
    "messages",
    [
        (_agent(0, content="Hi! How can I help you today?"), _user(1, "Hello?")),
        (
            _agent(0, content="Hi! How can I help you today?"),
            _user(1, "Hello?"),
            _agent(2, content='{"message": "  "}'),
        ),
    ],
)
def test_no_reply_after_the_opener_is_not_applicable_with_no_model_call(messages):
    judge, client = _clarity([])
    result, _ = _only(judge, messages)
    assert result.status is NOT_APPLICABLE
    assert client.requests == []


@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_a_trace_with_no_root_to_cite_is_insufficient_evidence_with_no_model_call(make):
    judge, client = make([])
    result, _ = _only_with_events(judge, _conversation(), ())
    assert result.status is UNKNOWN
    assert client.requests == []


@pytest.mark.parametrize("make", [_clarity, _escalation])
def test_a_run_with_no_messages_is_insufficient_evidence_with_no_model_call(make):
    judge, client = make([])
    subject = EvaluationSubject(SIM, _simulation(()))
    assert judge.evaluate(subject).results[0].status is UNKNOWN
    assert client.requests == []


# Escalation: the state check, its failure kind, and the rubric's kind.


@pytest.mark.parametrize(
    "reply",
    [
        TRANSFER_MESSAGE,
        json.dumps({"message": TRANSFER_MESSAGE}),
    ],
)
def test_a_correct_transfer_message_goes_on_to_the_rubric(reply):
    judge, client = _escalation([_reply(2)])
    result, _ = _only(judge, _transferred(reply))
    assert (result.status, result.score) == (PASS, 2)
    assert len(client.requests) == 1


def test_a_failed_transfer_message_is_a_violation_with_no_model_call():
    judge, client = _escalation([])
    result, _ = _only(judge, _transferred("Let me find someone for you."))
    assert result.status is FAIL
    assert result.failure_kind is FailureKind.VIOLATION
    assert result.score is None
    assert result.judge_run is None
    assert client.requests == []


def test_a_failed_state_check_stays_a_fail_in_an_invalid_run():
    judge, _ = _escalation([])
    result, _ = _only(judge, _transferred("Let me find someone for you."), "infrastructure_error")
    assert (result.status, result.failure_kind) == (FAIL, FailureKind.VIOLATION)


@pytest.mark.parametrize("score", [0, 1])
def test_a_low_escalation_score_is_an_outcome_and_leaves_an_invalid_run(score):
    judge, _ = _escalation([_reply(score)])
    result, _ = _only(judge, _transferred())
    assert (result.status, result.score, result.failure_kind) == (
        FAIL,
        score,
        FailureKind.OUTCOME,
    )
    judge, _ = _escalation([_reply(score)])
    invalid, _ = _only(judge, _transferred(), "infrastructure_error")
    assert invalid.status is ResultStatus.INVALID


def test_a_transfer_with_no_result_is_undecided_with_no_model_call():
    call = ToolCall("c1", _TRANSFER, {"summary": "Out of scope."})
    judge, client = _escalation([])
    result, _ = _only(judge, (_user(0, "Help."), _agent(1, call)))
    assert result.status is UNKNOWN
    assert client.requests == []


def test_a_run_that_ended_before_the_transfer_reply_is_undecided():
    judge, client = _escalation([])
    result, _ = _only(judge, _transferred()[:4], "max_steps")
    assert result.status is UNKNOWN
    assert client.requests == []


# Escalation: the judge reports that no request could have needed a transfer.


def test_no_transfer_and_the_judge_reports_none_needed_is_not_applicable():
    judge, _ = _escalation([_reply(2, no_transfer_needed=True)])
    result, output = _only(judge, _conversation())
    assert (result.status, result.score, result.failure_kind) == (NOT_APPLICABLE, None, None)
    assert output.raw_outputs[0].payload["no_transfer_needed"] is True
    assert result.judge_run.prompt_version == "escalation-v1"


def test_a_run_that_ended_early_cannot_show_no_transfer_was_needed():
    judge, _ = _escalation([_reply(2, no_transfer_needed=True)])
    result, _ = _only(judge, _conversation(), "max_steps")
    assert (result.status, result.score) == (UNKNOWN, None)


def test_no_transfer_with_no_such_report_stays_in_the_denominator():
    judge, _ = _escalation([_reply(2, no_transfer_needed=False)])
    result, _ = _only(judge, _conversation())
    assert result.status is PASS


@pytest.mark.parametrize("score", [0, 1])
def test_a_low_score_is_never_removed_from_the_denominator(score):
    judge, _ = _escalation([_reply(score, no_transfer_needed=True)])
    result, _ = _only(judge, _conversation())
    assert (result.status, result.failure_kind) == (FAIL, FailureKind.OUTCOME)


def test_an_uncertain_judge_cannot_remove_a_conversation_from_the_denominator():
    judge, _ = _escalation([_reply(2, uncertain=True, no_transfer_needed=True)])
    result, _ = _only(judge, _conversation())
    assert result.status is UNKNOWN


@pytest.mark.parametrize("error", [False, True], ids=["made", "refused by the tool"])
def test_a_transfer_contradicts_the_report_and_a_top_score(error):
    judge, _ = _escalation([_reply(2, no_transfer_needed=True)])
    messages = _transferred(error=error)
    result, _ = _only(judge, messages)
    assert result.status is ERROR


def test_a_transfer_the_agent_should_not_have_made_stays_a_fail_beside_the_report():
    judge, _ = _escalation([_reply(0, no_transfer_needed=True)])
    result, _ = _only(judge, _transferred())
    assert (result.status, result.score) == (FAIL, 0)


def test_a_transfer_report_that_is_not_a_boolean_is_an_evaluator_error():
    judge, _ = _escalation([_reply(2, no_transfer_needed="yes")])
    result, _ = _only(judge, _conversation())
    assert result.status is ERROR


def test_only_the_escalation_judge_can_give_not_applicable_from_a_reply():
    judge, _ = _clarity([_reply(2, no_transfer_needed=True)])
    result, _ = _only(judge, _conversation())
    assert result.status is PASS


# The JSON wrapper on agent replies.


def test_a_judge_sees_the_wording_and_not_the_wrapper():
    judge, client = _clarity([_reply()])
    _, output = _only(judge, _conversation())
    conversation = json.loads(client.requests[0].messages[0].content)["messages"]
    assert conversation[2] == {"role": "assistant", "content": "Your order has shipped."}
    assert '\\"message\\"' not in client.requests[0].messages[0].content
    assert output.raw_outputs[0].payload["call"]["unwrapped_replies"] == 1


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"message": "Plain."}', "Plain."),
        ('{"message": "Line one.\nLine two."}', "Line one.\nLine two."),
        ("Plain text.", "Plain text."),
        ('{"message": "Words.", "extra": 1}', '{"message": "Words.", "extra": 1}'),
        ('{"message": 7}', '{"message": 7}'),
        ('["message"]', '["message"]'),
        ('{"message": "Cut o', '{"message": "Cut o'),
    ],
    ids=[
        "wrapped",
        "wrapped with a raw newline, as task 1 of the baseline",
        "plain",
        "another key",
        "not text",
        "a list",
        "truncated",
    ],
)
def test_only_the_exact_wrapper_comes_off_a_reply(content, expected):
    assert reply_text(content) == expected


def test_the_wrapper_is_taken_off_agent_replies_and_nothing_else():
    wrapped = '{"message": "Kept."}'
    messages = (
        _user(0, wrapped),
        _agent(1, ToolCall("c1", "get_order_details", {"order_id": "#W1"})),
        _result(2, "c1", wrapped),
        _agent(3, content=wrapped),
    )
    view = judge_view(messages)
    assert view[0]["content"] == wrapped
    assert view[2]["content"] == wrapped
    assert view[3]["content"] == "Kept."


def test_the_all_messages_shape_keeps_tool_calls_and_results():
    view = judge_view(_transferred())
    assert [message["role"] for message in view] == [
        "assistant",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert view[2]["tool_calls"][0]["function"]["name"] == _TRANSFER
    assert view[3]["tool_call_id"] == "c1"


def test_the_transfer_state_check_also_unwraps_a_reply_with_a_raw_newline():
    reply = '{"message": "' + TRANSFER_MESSAGE.replace(" PLEASE", "\n PLEASE") + '"}'
    assert "\n" in reply_text(reply)
    (result,) = TransferMessageEvaluator().evaluate(_subject(_transferred(reply))).results
    assert result.status is FAIL  # the words differ, and it was read, not left wrapped
    assert "not the message" in result.reason


# The answer key.

KEY_SENTINELS = {
    "evaluation_criteria": "SENTINEL-EVALUATION-CRITERIA",
    "user_scenario": "SENTINEL-USER-SCENARIO",
    "nl_assertions": "SENTINEL-NL-ASSERTIONS",
    "action_checks": "SENTINEL-ACTION-CHECKS",
}
REWARD_SENTINEL = 0.31415926


@pytest.fixture
def answer_key_subject(tmp_path):
    """A saved run whose answer key is marked everywhere it is stored."""
    data = json.loads(SAMPLE_RUN.read_text("utf-8"))
    for task in data["tasks"]:
        task["user_scenario"] = {"instructions": KEY_SENTINELS["user_scenario"]}
        task["evaluation_criteria"] = {
            "actions": [{"name": KEY_SENTINELS["evaluation_criteria"], "arguments": {}}],
            "nl_assertions": [KEY_SENTINELS["nl_assertions"]],
            "reward_basis": ["DB"],
        }
    for simulation in data["simulations"]:
        simulation["reward_info"] = {
            "reward": REWARD_SENTINEL,
            "reward_basis": ["DB"],
            "reward_breakdown": {"DB": REWARD_SENTINEL},
            "nl_assertions": [{"nl_assertion": KEY_SENTINELS["nl_assertions"], "met": True}],
            "action_checks": [{"action": {"name": KEY_SENTINELS["action_checks"]}}],
            "note": KEY_SENTINELS["evaluation_criteria"],
        }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data), "utf-8")
    run = read_results(path, DENVER)
    (simulation,) = run.simulations
    events = evidence_trace(
        SavedRun("run", None, None, None, None, (simulation,)),
        simulation,
        ExperimentClass.CUSTOM_LAB_EXPERIMENT,
    )
    return EvaluationSubject(simulation.simulation_id, simulation, events)


def test_the_answer_key_never_reaches_a_judges_input(answer_key_subject):
    # The record the evaluator receives does hold the key, so the check below can fail.
    held = repr(answer_key_subject.simulation.reward.details)
    assert KEY_SENTINELS["nl_assertions"] in held
    assert KEY_SENTINELS["action_checks"] in held

    client = ScriptedModelClient([_reply(), _reply(), _reply()])
    output = evaluate_simulation(answer_key_subject, rubric_judges(client))
    assert len(client.requests) == 3
    assert all(result.status is PASS for result in output.results)
    for request in client.requests:
        sent = json.dumps(
            {
                "system": request.system,
                "messages": [message.content for message in request.messages],
                "settings": dict(request.settings),
            }
        )
        for sentinel in (*KEY_SENTINELS.values(), str(REWARD_SENTINEL)):
            assert sentinel not in sent
        for field in KEY_SENTINELS:
            assert field not in request.messages[0].content
        assert "reward" not in request.messages[0].content


# The prompts.


@pytest.mark.parametrize("version", PROMPT_SHA256)
def test_a_changed_prompt_is_a_new_version(version):
    digest = hashlib.sha256(judge_prompt(version).encode("utf-8")).hexdigest()
    assert digest == PROMPT_SHA256[version], f"{version} changed: add a new file and version"


@pytest.mark.parametrize("version", PROMPT_SHA256)
def test_each_prompt_states_its_anchors_and_reply_fields(version):
    prompt = judge_prompt(version)
    for part in ("**0,", "**1,", "**2,", '"score"', '"evidence"', '"uncertain"'):
        assert part in prompt
    assert ("no_transfer_needed" in prompt) is (version == "escalation-v1")


def test_each_judge_version_names_a_prompt():
    versions = {judge.version for judge in rubric_judges(ScriptedModelClient())}
    assert versions == set(PROMPT_SHA256)


# The recording of real calls on the Milestone 2 transcript of task 0.


def _task_0_subject() -> EvaluationSubject:
    ((_, subject),) = saved_subjects([str(TASK_0)])
    return subject


def test_a_judged_result_replays_from_the_cassette_with_no_model_call():
    with replaying(Cassette.load(RECORDING)) as transport:
        output = evaluate_simulation(_task_0_subject(), rubric_judges(LiteLLMModelClient()))
    transport.check_complete()
    assert transport.served == 3
    by_key = {result.evaluation_key: result for result in output.results}
    assert (by_key[CLARITY_KEY].status, by_key[CLARITY_KEY].score) == (PASS, 2)
    assert (by_key[EFFICIENCY_KEY].status, by_key[EFFICIENCY_KEY].score) == (PASS, 2)
    assert by_key[ESCALATION_KEY].status is NOT_APPLICABLE
    for result in output.results:
        assert result.judge_run.settings_sent == JUDGE_SETTINGS
        assert result.judge_run.model.startswith(JUDGE_MODEL)


def test_the_recorded_requests_carry_the_prompts_and_the_unwrapped_conversation():
    interactions = Cassette.load(RECORDING).interactions
    assert len(interactions) == 3
    for interaction, version in zip(interactions, PROMPT_SHA256, strict=True):
        body = interaction.request.body
        assert body["model"] == JUDGE_MODEL
        assert {k: body[k] for k in JUDGE_SETTINGS} == JUDGE_SETTINGS
        system = body["system"]
        text = system if isinstance(system, str) else system[0]["text"]
        assert text == judge_prompt(version)
        assert interaction.response.status == 200
        assert '\\"message\\"' not in json.dumps(body["messages"][0]["content"])


def test_replay_fails_if_the_judge_sends_anything_else():
    messages = (_user(0, "Something else."), _agent(1, content="A different reply."))
    simulation = _simulation(messages)
    subject = EvaluationSubject(SIM, simulation, _events(simulation))
    with replaying(Cassette.load(RECORDING)):
        output = evaluate_simulation(subject, rubric_judges(LiteLLMModelClient()))
    assert all(result.status is ERROR for result in output.results)
