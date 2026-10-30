"""What behavioral equivalence compares between two runs of one task.

Observability must not change behavior: on a deterministic replay of a known
task, enabling tracing may change no agent response, tool call, final state,
or benchmark outcome (DEVELOPMENT.md, Milestone 3). A ``RunOutcome`` holds
exactly those, taken from one run, so two runs can be compared field by
field. Timing, cost, and IDs are left out: they differ between any two runs.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from agent_autonomy_lab.domain.events import json_data
from agent_autonomy_lab.domain.simulations import Role, SavedSimulation


@dataclass(frozen=True)
class RunOutcome:
    """The compared results of one run; every field is strict JSON data.

    ``transcript`` is every message's role, content, tool calls, and tool
    result fields, in order. ``agent_messages`` and ``tool_calls`` repeat
    the agent's part of it so a difference is reported by name.
    ``final_db_hash`` is read from the run's live environment after the
    conversation, not from the evaluator's replay. ``reward_details`` holds
    the checks behind the reward, including the task's answer key.
    """

    agent_messages: list[str | None]
    tool_calls: list[dict[str, Any]]
    transcript: list[dict[str, Any]]
    final_db_hash: str
    termination_reason: str | None
    reward: float | None
    reward_breakdown: dict[str, float] | None
    reward_details: dict[str, Any] | None

    def __post_init__(self) -> None:
        for item in fields(self):
            object.__setattr__(self, item.name, json_data(getattr(self, item.name)))

    @classmethod
    def of(cls, simulation: SavedSimulation, final_db_hash: str) -> "RunOutcome":
        messages = simulation.messages
        reward = simulation.reward
        return cls(
            agent_messages=[m.content for m in messages if m.role is Role.ASSISTANT],
            tool_calls=[
                {"name": call.name, "arguments": call.arguments}
                for m in messages
                for call in m.tool_calls
            ],
            transcript=[
                {
                    "role": m.role.value,
                    "content": m.content,
                    "tool_calls": [
                        {"id": c.call_id, "name": c.name, "arguments": c.arguments}
                        for c in m.tool_calls
                    ],
                    "tool_call_id": m.tool_call_id,
                    "error": m.error,
                    "execution_status": m.execution_status.value if m.execution_status else None,
                }
                for m in messages
            ],
            final_db_hash=final_db_hash,
            termination_reason=simulation.termination_reason,
            reward=reward.reward if reward else None,
            reward_breakdown=dict(reward.breakdown) if reward else None,
            reward_details=dict(reward.details) if reward else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunOutcome":
        return cls(**{item.name: data[item.name] for item in fields(cls)})

    def differences(self, other: "RunOutcome") -> list[str]:
        """Names of the fields whose values differ from ``other``'s."""
        return [
            item.name
            for item in fields(self)
            if getattr(self, item.name) != getattr(other, item.name)
        ]


COMPARED_FIELDS = tuple(item.name for item in fields(RunOutcome))
