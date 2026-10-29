"""Export a saved simulation run as threaded traces (hook D, docs/BASELINE.md).

Each simulation becomes one ``conversation`` root span whose ``messages``
hold the whole transcript in OpenAI chat format, with a ``model_call`` child
for every message a model produced and a ``tool_call`` child for every tool
result. The benchmark reward is recorded as feedback on the root, outside the
messages. Every ID is derived from the simulation, so exporting the same run
again produces the same threads, runs, and feedback instead of duplicates.

A thread needs messages on both sides of the root, so a simulation with fewer
than two messages (for example one that failed before the customer spoke) has
no conversation to show; it is skipped and reported, not exported.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Feedback,
    Outcome,
    RunContext,
    TraceEvent,
    argument_hash,
    policy_version,
    stable_id,
    stable_time_id,
    thread_id_for,
)
from agent_autonomy_lab.domain.simulations import (
    ExecutionStatus,
    Message,
    Role,
    SavedRun,
    SavedSimulation,
    ToolCall,
)
from agent_autonomy_lab.ports.trace_sink import TraceSink

REWARD_KEY = "reward"
MIN_MESSAGES = 2


@dataclass(frozen=True)
class ExportedSimulation:
    """One simulation's export; ``skipped`` gives the reason when nothing was recorded."""

    simulation_id: str
    task_id: str
    thread_id: str
    root_run_id: UUID | None
    spans: int
    feedback: int
    skipped: str | None = None


def export_run(
    run: SavedRun, sink: TraceSink, experiment_class: ExperimentClass
) -> list[ExportedSimulation]:
    """Record every simulation of ``run`` in ``sink``, then flush it once.

    A sink that checks trace shape rejects the whole run if any simulation is
    malformed, so a run is never half exported. A simulation with too few
    messages to form a conversation is skipped and returned with its reason.
    """
    exported = []
    for simulation in run.simulations:
        if len(simulation.messages) < MIN_MESSAGES:
            exported.append(
                ExportedSimulation(
                    simulation_id=simulation.simulation_id,
                    task_id=simulation.task_id,
                    thread_id=thread_id_for(simulation.simulation_id),
                    root_run_id=None,
                    spans=0,
                    feedback=0,
                    skipped=f"{len(simulation.messages)} message(s); a conversation needs "
                    f"{MIN_MESSAGES}",
                )
            )
            continue
        events, feedback = simulation_trace(run, simulation, experiment_class)
        for event in events:
            sink.record(event)
        for item in feedback:
            sink.record_feedback(item)
        exported.append(
            ExportedSimulation(
                simulation_id=simulation.simulation_id,
                task_id=simulation.task_id,
                thread_id=events[0].context.thread_id,
                root_run_id=events[0].run_id,
                spans=len(events),
                feedback=len(feedback),
            )
        )
    sink.flush()
    return exported


def simulation_trace(
    run: SavedRun, simulation: SavedSimulation, experiment_class: ExperimentClass
) -> tuple[list[TraceEvent], list[Feedback]]:
    """Return the trace events, root first, and the reward feedback for one simulation.

    Raises ``ValueError`` for a simulation with fewer than ``MIN_MESSAGES`` messages.
    """
    if len(simulation.messages) < MIN_MESSAGES:
        raise ValueError(
            f"simulation {simulation.simulation_id}: {len(simulation.messages)} message(s) "
            "cannot form a conversation"
        )
    context = RunContext(
        simulation_id=simulation.simulation_id,
        thread_id=thread_id_for(simulation.simulation_id),
        task_id=simulation.task_id,
        autonomy_level=NO_GATE,
        experiment_class=experiment_class,
        agent_version=run.agent_implementation,
        model_id=run.model_id,
        user_model_id=run.user_model_id,
        policy_version=policy_version(run.policy_text) if run.policy_text else None,
    )
    sim_id = simulation.simulation_id
    root_id = stable_time_id(_ms(simulation.start_time), "run", sim_id, "conversation")
    inputs, outputs = _split_transcript(simulation.messages)
    root = TraceEvent(
        event_id=stable_id("event", sim_id, "conversation"),
        event_type=EventType.CONVERSATION,
        timestamp=simulation.start_time,
        context=context,
        run_id=root_id,
        elapsed_ms=simulation.duration_seconds * 1000,
        inputs={"messages": [_chat_message(m) for m in inputs]},
        outputs={"messages": [_chat_message(m) for m in outputs]},
    )
    events = [root]
    calls: dict[str, ToolCall] = {}
    for message in simulation.messages:
        calls.update((call.call_id, call) for call in message.tool_calls)
        if message.is_model_output:
            events.append(_model_call(message, context, root_id))
        elif message.role is Role.TOOL:
            call = calls.get(message.tool_call_id)
            if call is None:
                raise ValueError(
                    f"simulation {sim_id}: tool result {message.tool_call_id} answers no tool call"
                )
            events.append(_tool_call(message, call, context, root_id))
    feedback = _reward_feedback(simulation, context, root_id)
    return events, feedback


def _ms(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1000)


def _split_transcript(messages: tuple[Message, ...]) -> tuple[list[Message], list[Message]]:
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


def _chat_message(message: Message) -> dict[str, Any]:
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


def _model_call(message: Message, context: RunContext, root_id: UUID) -> TraceEvent:
    """A model call ends when its message is written, so it starts its generation time earlier."""
    elapsed_ms = message.generation_seconds * 1000 if message.generation_seconds else None
    start = message.timestamp - timedelta(milliseconds=elapsed_ms or 0)
    sim_id = context.simulation_id
    return TraceEvent(
        event_id=stable_id("event", sim_id, "model_call", str(message.turn_index)),
        event_type=EventType.MODEL_CALL,
        timestamp=start,
        context=context,
        run_id=stable_time_id(_ms(start), "run", sim_id, "model_call", str(message.turn_index)),
        parent_run_id=root_id,
        turn_index=message.turn_index,
        elapsed_ms=elapsed_ms,
        tokens=message.tokens,
        cost=message.cost,
        outputs={"messages": [_chat_message(message)]},
    )


def _tool_call(message: Message, call: ToolCall, context: RunContext, root_id: UUID) -> TraceEvent:
    """One tool result and the call it answers. τ-bench records no tool timing."""
    if message.execution_status is ExecutionStatus.REJECTED:
        outcome = Outcome.REJECTED
    else:
        outcome = Outcome.ERROR if message.error else Outcome.SUCCESS
    sim_id = context.simulation_id
    return TraceEvent(
        event_id=stable_id("event", sim_id, "tool_call", call.call_id),
        event_type=EventType.TOOL_CALL,
        timestamp=message.timestamp,
        context=context,
        run_id=stable_time_id(_ms(message.timestamp), "run", sim_id, "tool_call", call.call_id),
        parent_run_id=root_id,
        turn_index=message.turn_index,
        tool_name=call.name,
        argument_hash=argument_hash(call.arguments),
        outcome=outcome,
        error_message=(message.content or None) if outcome is Outcome.ERROR else None,
        inputs=call.arguments,
        outputs={"content": message.content},
    )


def _reward_feedback(
    simulation: SavedSimulation, context: RunContext, root_id: UUID
) -> list[Feedback]:
    """The reward, with its checks as the value, and one feedback per breakdown component."""
    reward = simulation.reward
    if reward is None:
        return []
    sim_id = simulation.simulation_id

    def feedback(key: str, score: float, **fields: Any) -> Feedback:
        return Feedback(
            feedback_id=stable_id("feedback", sim_id, key),
            run_id=root_id,
            context=context,
            key=key,
            score=score,
            **fields,
        )

    value = {
        "reward_basis": list(reward.basis),
        "reward_breakdown": dict(reward.breakdown),
        "termination_reason": simulation.termination_reason,
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
