"""Normalize what a judge stored into a result, so no judge output goes missing.

A judge failure can be stored with no score: in Milestone 3 a System-only prompt
stored its provider error that way (docs/ARCHITECTURE.md, "Thread evaluator
validation"). That is an ``EVALUATOR_ERROR`` row, never an absent one.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from agent_autonomy_lab.domain.evaluation.results import (
    EvaluationResult,
    EvaluatorOutput,
    FailureKind,
    JudgeRun,
    RawEvaluatorOutput,
    ResultStatus,
)
from agent_autonomy_lab.domain.events import json_data, new_id
from agent_autonomy_lab.domain.model_calls import ModelMessage, ModelRequest

RUBRIC_PASS_SCORE = 2
RUBRIC_SCORES = (0, 1, RUBRIC_PASS_SCORE)

JUDGE_MODEL = "claude-haiku-5-5"
JUDGE_SETTINGS: Mapping[str, Any] = {"max_tokens": 2048, "output_config": {"effort": "low"}}
"""Declared by their names in the provider's request (docs/DECISIONS.md, ADR-020)."""


def judge_request(system: str, user: str) -> ModelRequest:
    """One judge's request: the rubric as the system prompt, the evidence as the Human message."""
    return ModelRequest(
        model=JUDGE_MODEL,
        system=system,
        messages=(ModelMessage("user", user),),
        settings=JUDGE_SETTINGS,
    )


def _raw_payload(stored: Any) -> dict[str, Any]:
    """Keep whatever was stored, including a value that is not JSON or not a mapping."""
    try:
        return (
            dict(json_data(stored))
            if isinstance(stored, Mapping)
            else {"stored": json_data(stored)}
        )
    except (TypeError, ValueError):
        return {"unparsable": repr(stored)}


def judge_reply(text: str) -> dict[str, Any]:
    """Read a judge's reply: a JSON object with its evidence and uncertainty flag.

    A reply that is anything else comes back as a ``problem``, which ``judge_output``
    reports as ``EVALUATOR_ERROR``. Whether the score is valid is left to it.
    """
    try:
        parsed = json_data(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"problem": "it is not strict JSON", "reply": text}
    if not isinstance(parsed, dict):
        return {"problem": "it is not a JSON object", "reply": text}
    if not isinstance(parsed.get("evidence"), str) or not parsed["evidence"].strip():
        return {"problem": "it has no evidence", "reply": parsed}
    if not isinstance(parsed.get("uncertain"), bool):
        return {"problem": "it has no boolean uncertainty flag", "reply": parsed}
    return parsed


def _no_transfer_needed_status(
    stored: Mapping[str, Any], score: int, transfer_made: bool | None
) -> tuple[ResultStatus, int | None, str] | None:
    """The escalation judge's report that no request could have needed a transfer.

    Only a score of 2 can leave the denominator, so a low score always stands. A
    transfer that was made contradicts a report and a score of 2 together, so the reply
    cannot be used. ``transfer_made`` is ``None`` for a judge with no such report.
    """
    if transfer_made is None or score != RUBRIC_PASS_SCORE:
        return None
    if not stored.get("no_transfer_needed", False):
        return None
    if transfer_made:
        return (
            ResultStatus.EVALUATOR_ERROR,
            None,
            "The judge scored 2 and reported no request needed a transfer, but one was made.",
        )
    return (
        ResultStatus.NOT_APPLICABLE,
        None,
        "No transfer was made, and the judge reports no request could have needed one.",
    )


def _status_and_score(
    stored: Any, transfer_made: bool | None
) -> tuple[ResultStatus, int | None, str]:
    """Return the status, score, and default reason for one stored judge output."""
    if not isinstance(stored, Mapping):
        return ResultStatus.EVALUATOR_ERROR, None, "The judge's output is not a mapping."
    if "problem" in stored:
        return ResultStatus.EVALUATOR_ERROR, None, f"The judge's reply failed: {stored['problem']}."
    score = stored.get("score")
    if score is None:
        return ResultStatus.EVALUATOR_ERROR, None, "The judge stored no score."
    if isinstance(score, bool) or not isinstance(score, (int, float)) or score not in RUBRIC_SCORES:
        return (
            ResultStatus.EVALUATOR_ERROR,
            None,
            f"The judge's score is not one of {RUBRIC_SCORES}.",
        )
    uncertain = stored.get("uncertain", False)
    if not isinstance(uncertain, bool):
        return ResultStatus.EVALUATOR_ERROR, None, "The judge's uncertainty flag is not a boolean."
    if transfer_made is not None and not isinstance(stored.get("no_transfer_needed", False), bool):
        return ResultStatus.EVALUATOR_ERROR, None, "The judge's transfer report is not a boolean."
    if uncertain:
        return ResultStatus.INSUFFICIENT_EVIDENCE, None, "The judge raised its uncertainty flag."
    not_needed = _no_transfer_needed_status(stored, score, transfer_made)
    if not_needed is not None:
        return not_needed
    status = ResultStatus.PASS if score == RUBRIC_PASS_SCORE else ResultStatus.FAIL
    return status, int(score), f"The judge scored {int(score)}."


def _judge_reason(stored: Any) -> str | None:
    """The judge's own words: ``evidence`` from a lab judge, ``reason`` from a stored output."""
    for key in ("evidence", "reason"):
        if isinstance(stored.get(key), str) and stored[key]:
            return stored[key]
    return None


def judge_output(
    stored: Any,
    *,
    evaluation_key: str,
    evaluator_version: str,
    simulation_id: str,
    failure_kind: FailureKind,
    evidence_ids: Iterable[UUID],
    scenario_id: str | None = None,
    target: str | None = None,
    judge_run: JudgeRun | None = None,
    call: Mapping[str, Any] | None = None,
    transfer_made: bool | None = None,
) -> EvaluatorOutput:
    """Turn one stored 0–2 judge output into one result and its raw output.

    A missing or malformed score gives ``EVALUATOR_ERROR``, even with an
    uncertainty flag, because the contract treats a judge stored with no score as
    a failure. Otherwise an uncertainty flag gives ``INSUFFICIENT_EVIDENCE``, and
    only a score of 2 is a ``PASS``. The judge's reason is used only on a ``PASS`` or
    ``FAIL``; it stays readable in the raw output otherwise. A ``PASS`` or ``FAIL``
    with no ``evidence_ids`` raises ``ValueError``, as any such result does.

    ``transfer_made`` turns on the escalation judge's ``no_transfer_needed`` report,
    which can give ``NOT_APPLICABLE``. ``call`` holds what the call used, for audit.
    """
    payload = _raw_payload(stored)
    raw = RawEvaluatorOutput(
        output_id=new_id(),
        evaluation_key=evaluation_key,
        evaluator_version=evaluator_version,
        simulation_id=simulation_id,
        payload={**payload, "call": dict(call)} if call else payload,
    )
    status, score, reason = _status_and_score(stored, transfer_made)
    if score is not None:
        reason = _judge_reason(stored) or reason
    result = EvaluationResult(
        evaluation_key=evaluation_key,
        status=status,
        reason=reason,
        evaluator_version=evaluator_version,
        simulation_id=simulation_id,
        score=score,
        evidence_ids=tuple(evidence_ids),
        scenario_id=scenario_id,
        target=target,
        failure_kind=failure_kind if status is ResultStatus.FAIL else None,
        raw_output_id=raw.output_id,
        judge_run=judge_run,
    )
    return EvaluatorOutput(results=(result,), raw_outputs=(raw,))
