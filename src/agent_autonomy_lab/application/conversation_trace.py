"""Span and feedback builders shared by every way a simulation becomes a trace.

A saved run exported after the fact (``export_simulation``) and a live run
recorded by the agent wrapper (``trace_live_run``) both show a conversation as
OpenAI chat messages, a tool result as a ``tool_call`` span, and the
benchmark reward as feedback on the root. ``id_parts`` keeps their IDs apart:
the export passes none, so its IDs are unchanged, and the live trace passes
``("live",)``.
"""

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from agent_autonomy_lab.domain.events import (
    EventType,
    Feedback,
    Outcome,
    RunContext,
    TraceEvent,
    argument_hash,
    stable_id,
    stable_time_id,
)
from agent_autonomy_lab.domain.simulations import (
    Message,
    Reward,
    Role,
    ToolCall,
)

REWARD_KEY = "reward"
MIN_MESSAGES = 2


def epoch_ms(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1000)


def split_transcript(messages: Sequence[Message]) -> tuple[list[Message], list[Message]]:
    """Split after the first customer message: it and the opener are the inputs.

    A thread view joins inputs and outputs, so together they are the whole
    transcript in order. When nothing follows that message (or no customer
    message exists), the last message moves to the outputs, so both sides of
    a transcript of at least two messages are non-empty.
    """
    split = next(
        (index + 1 for index, message in enumerate(messages) if message.role is Role.USER),
        len(messages),
    )
    split = min(split, len(messages) - 1)
    return list(messages[:split]), list(messages[split:])


def chat_message(message: Message) -> dict[str, Any]:
    """The message as an OpenAI chat message, which LangSmith threads read."""
    chat: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        chat["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.role is Role.TOOL:
        chat["tool_call_id"] = message.tool_call_id
    return chat


def model_call_event(
    message: Message, context: RunContext, root_id: UUID, id_parts: tuple[str, ...] = ()
) -> TraceEvent:
    """A model call read from its message, which is written when the call ends.

    The call starts ``generation_seconds`` before the message's timestamp.
    """
    elapsed_ms = message.generation_seconds * 1000 if message.generation_seconds else None
    start = message.timestamp - timedelta(milliseconds=elapsed_ms or 0)
    sim_id = context.simulation_id
    turn = str(message.turn_index)
    return TraceEvent(
        event_id=stable_id("event", sim_id, *id_parts, "model_call", turn),
        event_type=EventType.MODEL_CALL,
        timestamp=start,
        context=context,
        run_id=stable_time_id(epoch_ms(start), "run", sim_id, *id_parts, "model_call", turn),
        parent_run_id=root_id,
        turn_index=message.turn_index,
        elapsed_ms=elapsed_ms,
        tokens=message.tokens,
        cost=message.cost,
        outputs={"messages": [chat_message(message)]},
    )


def tool_call_event(
    message: Message,
    call: ToolCall,
    context: RunContext,
    root_id: UUID,
    id_parts: tuple[str, ...] = (),
    error_class: str | None = None,
) -> TraceEvent:
    """One tool result and the call it answers. τ-bench records no tool timing.

    τ-bench does not classify a failed call; ``error_class`` is for a caller
    that knows why it failed.
    """
    outcome = message.tool_outcome
    sim_id = context.simulation_id
    return TraceEvent(
        event_id=stable_id("event", sim_id, *id_parts, "tool_call", call.call_id),
        event_type=EventType.TOOL_CALL,
        timestamp=message.timestamp,
        context=context,
        run_id=stable_time_id(
            epoch_ms(message.timestamp), "run", sim_id, *id_parts, "tool_call", call.call_id
        ),
        parent_run_id=root_id,
        turn_index=message.turn_index,
        tool_name=call.name,
        argument_hash=argument_hash(call.arguments),
        outcome=outcome,
        error_class=error_class,
        error_message=(message.content or None) if outcome is Outcome.ERROR else None,
        inputs=call.arguments,
        outputs={"content": message.content},
    )


def reward_feedback(
    reward: Reward | None,
    termination_reason: str | None,
    context: RunContext,
    root_id: UUID,
    id_parts: tuple[str, ...] = (),
) -> list[Feedback]:
    """The reward, with its checks as the value, and one feedback per breakdown component."""
    if reward is None:
        return []
    sim_id = context.simulation_id

    def feedback(key: str, score: float, **fields: Any) -> Feedback:
        return Feedback(
            feedback_id=stable_id("feedback", sim_id, *id_parts, key),
            run_id=root_id,
            context=context,
            key=key,
            score=score,
            **fields,
        )

    value = {
        "reward_basis": list(reward.basis),
        "reward_breakdown": dict(reward.breakdown),
        "termination_reason": termination_reason,
        **reward.details,
    }
    items = [
        feedback(
            REWARD_KEY,
            reward.reward,
            comment=_reward_comment(reward.breakdown.items()),
            value=value,
        )
    ]
    items += [
        feedback(f"{REWARD_KEY}_{name.lower()}", score) for name, score in reward.breakdown.items()
    ]
    return items


def _reward_comment(breakdown: Iterable[tuple[str, float]]) -> str:
    parts = ", ".join(f"{name} {score:g}" for name, score in breakdown)
    return f"τ-bench reward breakdown: {parts}" if parts else "τ-bench reward"
