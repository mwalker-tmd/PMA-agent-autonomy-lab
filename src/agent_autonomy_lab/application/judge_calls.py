"""What every lab judge shares: its versioned prompt, and one call made into one result.

A prompt's version is the evaluator's version, and a changed prompt is a new file
(docs/DECISIONS.md, ADR-020).
"""

import hashlib
from collections.abc import Iterable, Mapping
from importlib import resources
from typing import Any
from uuid import UUID

from agent_autonomy_lab.domain.evaluation import (
    EvaluationSubject,
    EvaluatorOutput,
    FailureKind,
    JudgeRun,
    judge_output,
    judge_reply,
    judge_request,
)
from agent_autonomy_lab.domain.model_calls import ModelCallError
from agent_autonomy_lab.ports.model_client import ModelClient


def judge_prompt(version: str) -> str:
    """The system prompt of one judge version, as it is in the repository."""
    prompts = resources.files("agent_autonomy_lab.application") / "judge_prompts"
    return (prompts / f"{version}.md").read_text(encoding="utf-8")


def call_judge(
    client: ModelClient,
    subject: EvaluationSubject,
    *,
    key: str,
    version: str,
    judged_text: str,
    failure_kind: FailureKind,
    evidence_ids: Iterable[UUID],
    target: str | None = None,
    call_fields: Mapping[str, Any] | None = None,
    transfer_made: bool | None = None,
) -> EvaluatorOutput:
    """Make one judge call and normalize what comes back, including a failed call.

    ``judged_text`` is everything the judge sees besides its prompt. The raw output keeps
    its hash and the call's usage, not the text.
    """
    request = judge_request(judge_prompt(version), judged_text)
    call: dict[str, Any] = {
        "input_sha256": hashlib.sha256(judged_text.encode("utf-8")).hexdigest(),
        **(call_fields or {}),
    }
    try:
        response = client.complete(request)
    except ModelCallError as error:
        stored = {
            "problem": "the model call failed",
            "error_class": type(error).__name__,
            "error_message": str(error),
        }
        judge_run = JudgeRun(version, request.model, None)
    else:
        stored = judge_reply(response.text)
        judge_run = JudgeRun(version, response.model, response.settings_sent)
        call |= {
            "finish_reason": response.finish_reason,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        }
    return judge_output(
        stored,
        evaluation_key=key,
        evaluator_version=version,
        simulation_id=subject.simulation_id,
        scenario_id=subject.scenario_id,
        target=target,
        failure_kind=failure_kind,
        evidence_ids=evidence_ids,
        judge_run=judge_run,
        call=call,
        transfer_made=transfer_made,
    )
