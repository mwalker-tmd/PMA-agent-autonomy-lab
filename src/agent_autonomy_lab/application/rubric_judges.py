"""The three rubric judges: clarity, conversation efficiency, and escalation.

Each judge scores one simulation on an anchored 0–2 rubric, with a versioned prompt in
``judge_prompts/`` (docs/DECISIONS.md, ADR-020 and ADR-021). A judge is given a
projection of the conversation built only from its messages, so the answer key the
simulation record holds cannot reach it, and the ``{"message": …}`` wrapper on most
agent replies is removed, so it scores the wording. The prompt's version is the
evaluator's version, and a changed prompt is a new file.

A low clarity or efficiency score is an ``outcome``. So is a low escalation score, while
a failed transfer message is a ``violation``, the kind PA-9 gives it, and it needs no
model call because no rubric score could change it.
"""

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from agent_autonomy_lab.application.check_results import CheckResults, record_or_note
from agent_autonomy_lab.application.conversation_trace import chat_message
from agent_autonomy_lab.application.judge_calls import call_judge
from agent_autonomy_lab.application.tool_correctness import (
    TRANSFER_TOOL,
    TransferMessageEvaluator,
)
from agent_autonomy_lab.domain.evaluation import (
    EvaluationResult,
    EvaluationSubject,
    EvaluatorOutput,
    FailureKind,
    ResultStatus,
)
from agent_autonomy_lab.domain.evaluation.run_standing import COMPLETED_REASONS
from agent_autonomy_lab.domain.evaluation.trace_evidence import TraceEvidence
from agent_autonomy_lab.domain.events import TraceEvent
from agent_autonomy_lab.domain.simulations import Message, Role, SavedSimulation, reply_text
from agent_autonomy_lab.domain.tool_timeline import tool_timeline
from agent_autonomy_lab.ports.model_client import ModelClient

CLARITY_KEY = "clarity_customer_effort"
EFFICIENCY_KEY = "conversation_efficiency"
ESCALATION_KEY = "escalation_correctness"

_NO_REPLY = "The agent sent no reply after the fixed opener."
_NO_ROOT = "The trace has no conversation span to cite for a judged result."


def _is_wrapped(message: Message) -> bool:
    return bool(
        message.role is Role.ASSISTANT
        and message.content
        and reply_text(message.content) != message.content
    )


def judge_view(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """The conversation in the ``all_messages`` shape, with the wrapper off agent replies.

    Only a message's role, content, and tool calls or tool result reach a judge. A
    tool's own output is left as it is, because the wrapper is on the agent's replies.
    """
    view = []
    for message in messages:
        chat = chat_message(message)
        if _is_wrapped(message):
            chat["content"] = reply_text(message.content)
        view.append(chat)
    return view


def _agent_replied(messages: Sequence[Message]) -> bool:
    """Whether the agent said anything after τ-bench's fixed opener, which is turn 0.

    Words beside a tool call count: they break the message form (PA-8), which its own check
    reports, and they are still words a rubric can score.
    """
    return any(
        message.role is Role.ASSISTANT
        and message.turn_index > 0
        and message.content
        and reply_text(message.content).strip()
        for message in messages
    )


def _judged(
    client: ModelClient,
    subject: EvaluationSubject,
    simulation: SavedSimulation,
    root: TraceEvent,
    key: str,
    version: str,
    transfer_made: bool | None = None,
) -> EvaluatorOutput:
    """One judge call on the whole conversation, in the judge's projection."""
    conversation = json.dumps(
        {"messages": judge_view(simulation.messages)}, ensure_ascii=False, indent=None
    )
    return call_judge(
        client,
        subject,
        key=key,
        version=version,
        judged_text=conversation,
        failure_kind=FailureKind.OUTCOME,
        evidence_ids=(root.event_id,),
        call_fields={
            "unwrapped_replies": sum(_is_wrapped(message) for message in simulation.messages)
        },
        transfer_made=transfer_made,
    )


class CustomerExperienceJudge:
    """One anchored rubric of the customer-experience metric; a low score is an ``outcome``."""

    def __init__(self, evaluation_key: str, version: str, client: ModelClient) -> None:
        self.evaluation_key = evaluation_key
        self.version = version
        self._client = client

    def evaluate(self, subject: EvaluationSubject) -> EvaluatorOutput:
        results = CheckResults(self.evaluation_key, self.version, subject)
        simulation = record_or_note(results, subject)
        if simulation is None:
            return results.output()
        if not _agent_replied(simulation.messages):
            return results.output(_NO_REPLY)
        root = TraceEvidence(subject.events).conversation_span()
        if root is None:
            results.unknown(None, _NO_ROOT)
            return results.output()
        return _judged(self._client, subject, simulation, root, self.evaluation_key, self.version)


class EscalationJudge:
    """Escalation correctness: the transfer message's state check, then the rubric.

    The unit is the simulation, so the state check's per-transfer results collapse into
    one. Any failed transfer message is a ``FAIL`` with no score, because the rubric did
    not run, so it stays out of the 0/1/2 distribution. A transfer attempt that the tool
    refused moved no one, but the agent still tried to escalate, so it closes the
    ``NOT_APPLICABLE`` path.
    """

    evaluation_key = ESCALATION_KEY
    version = "escalation-v1"

    def __init__(self, client: ModelClient) -> None:
        self._client = client

    def evaluate(self, subject: EvaluationSubject) -> EvaluatorOutput:
        results = CheckResults(self.evaluation_key, self.version, subject)
        simulation = record_or_note(results, subject)
        if simulation is None:
            return results.output()
        state = TransferMessageEvaluator().evaluate(subject).results
        failed = [result for result in state if result.status is ResultStatus.FAIL]
        if failed:
            return EvaluatorOutput(results=(self._state_failure(subject, failed),))
        undecided = next((r for r in state if r.status is ResultStatus.INSUFFICIENT_EVIDENCE), None)
        if undecided is not None:
            results.unknown(None, f"The transfer's state check is undecided: {undecided.reason}")
            return results.output()
        root = TraceEvidence(subject.events).conversation_span()
        if root is None:
            results.unknown(None, _NO_ROOT)
            return results.output()
        transfer_made = any(
            record.call.name == TRANSFER_TOOL for record in tool_timeline(simulation.messages)
        )
        output = _judged(
            self._client,
            subject,
            simulation,
            root,
            self.evaluation_key,
            self.version,
            transfer_made=transfer_made,
        )
        return self._with_whole_record(output, simulation)

    @staticmethod
    def _with_whole_record(output: EvaluatorOutput, simulation: SavedSimulation) -> EvaluatorOutput:
        """Only a finished conversation shows that no request could have needed a transfer."""
        (result,) = output.results
        if (
            result.status is not ResultStatus.NOT_APPLICABLE
            or simulation.termination_reason in COMPLETED_REASONS
        ):
            return output
        undecided = replace(
            result,
            status=ResultStatus.INSUFFICIENT_EVIDENCE,
            reason="The judge reports no request could have needed a transfer, but the run "
            "ended early, so a later request could have.",
        )
        return EvaluatorOutput(results=(undecided,), raw_outputs=output.raw_outputs)

    def _state_failure(
        self, subject: EvaluationSubject, failed: Sequence[EvaluationResult]
    ) -> EvaluationResult:
        evidence = dict.fromkeys(event_id for r in failed for event_id in r.evidence_ids)
        return EvaluationResult(
            evaluation_key=self.evaluation_key,
            status=ResultStatus.FAIL,
            reason=f"The transfer's state check failed: {failed[0].reason}",
            evaluator_version=self.version,
            simulation_id=subject.simulation_id,
            scenario_id=subject.scenario_id,
            evidence_ids=tuple(evidence),
            failure_kind=FailureKind.VIOLATION,
        )


def rubric_judges(client: ModelClient) -> tuple:
    """The three rubric judges, in the order they run."""
    return (
        CustomerExperienceJudge(CLARITY_KEY, "clarity-v1", client),
        CustomerExperienceJudge(EFFICIENCY_KEY, "efficiency-v1", client),
        EscalationJudge(client),
    )
