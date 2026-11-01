"""Read τ-bench's reward for a simulation, without recomputing or merging it.

``task_success`` is the benchmark's result (docs/EVALUATION.md, "Task success"). It is
answer-key data and stays apart from the lab's own checks: no score combines them
(ADR-008, ADR-019). ``partial_action_reward`` is reported as a diagnostic only, because
it measures similarity to one reference trajectory, not whether the writes were right.
"""

from collections.abc import Mapping
from typing import Any

from agent_autonomy_lab.application.check_results import CheckResults
from agent_autonomy_lab.domain.evaluation import EvaluationSubject, EvaluatorOutput, FailureKind
from agent_autonomy_lab.domain.evaluation.trace_evidence import TraceEvidence
from agent_autonomy_lab.domain.simulations import SavedSimulation

TASK_SUCCESS_KEY = "task_success"
_NO_CRITERIA_NOTE = "No evaluation criteria"


class TaskSuccessEvaluator:
    """``PASS`` when τ-bench's reward is ``1.0``, otherwise an ``outcome`` ``FAIL``.

    A reward with no basis proves nothing: τ-bench gives ``1.0`` to a task with no
    evaluation criteria, and ``0.0`` with no basis to a conversation that ended early.
    The reward feedback sits on the conversation root, so a result cites that span.
    """

    evaluation_key = TASK_SUCCESS_KEY
    version = "task-success-v1"

    def evaluate(self, subject: EvaluationSubject) -> EvaluatorOutput:
        results = CheckResults(self.evaluation_key, self.version, subject)
        if subject.simulation is not None and subject.scenario_id is None:
            return results.output("The simulation has no τ-bench task.")
        reward = subject.simulation.reward if subject.simulation else None
        if reward is None:
            results.unknown(None, "The run has no τ-bench reward.")
            return results.output()
        if not reward.basis and reward.note == _NO_CRITERIA_NOTE:
            return results.output("The task has no evaluation criteria, so its reward is empty.")
        if not reward.basis and reward.reward == 1.0:
            results.unknown(None, "τ-bench gave 1.0 with no reward basis, which proves nothing.")
            return results.output()
        root = TraceEvidence(subject.events).conversation_span()
        cited = (root.event_id,) if root else ()
        summary = f"τ-bench reward {reward.reward} on basis {list(reward.basis)}."
        if reward.reward == 1.0:
            results.passed(None, summary, cited)
        else:
            results.failed(None, summary, cited, FailureKind.OUTCOME)
        return results.output()


def _nl_assertions(details: Mapping[str, Any]) -> dict[str, int] | None:
    assertions = details.get("nl_assertions")
    if not assertions:
        return None
    return {"met": sum(1 for a in assertions if a.get("met") is True), "total": len(assertions)}


def _partial_action_reward(details: Mapping[str, Any]) -> dict | None:
    """τ-bench's ``partial_action_reward`` property, which ``results.json`` does not store."""
    checks = details.get("action_checks")
    if not checks:
        return None

    def share(selected: list) -> dict[str, int] | None:
        if not selected:
            return None
        return {"matched": sum(1 for c in selected if c.get("action_match")), "of": len(selected)}

    return {
        "total": share(list(checks)),
        "read": share([c for c in checks if c.get("tool_type") == "read"]),
        "write": share([c for c in checks if c.get("tool_type") == "write"]),
    }


def benchmark_report(simulation: SavedSimulation) -> dict[str, Any]:
    """The reward and its components as τ-bench gave them, for reporting beside the lab's checks."""
    reward = simulation.reward
    report: dict[str, Any] = {"termination_reason": simulation.termination_reason, "reward": None}
    if reward is None:
        return report
    return {
        **report,
        "reward": reward.reward,
        "reward_basis": list(reward.basis),
        "reward_breakdown": dict(reward.breakdown),
        "db_match": reward.db_match,
        "nl_assertions": _nl_assertions(reward.details),
        "diagnostics": {"partial_action_reward": _partial_action_reward(reward.details)},
    }
