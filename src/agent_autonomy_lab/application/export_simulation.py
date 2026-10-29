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

from dataclasses import dataclass
from uuid import UUID

from agent_autonomy_lab.application.conversation_trace import (
    MIN_MESSAGES,
    chat_message,
    epoch_ms,
    model_call_event,
    reward_feedback,
    split_transcript,
    tool_call_event,
)
from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Feedback,
    RunContext,
    TraceEvent,
    policy_version,
    stable_id,
    stable_time_id,
    thread_id_for,
)
from agent_autonomy_lab.domain.simulations import Role, SavedRun, SavedSimulation, ToolCall
from agent_autonomy_lab.ports.trace_sink import TraceSink


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
    root_id = stable_time_id(epoch_ms(simulation.start_time), "run", sim_id, "conversation")
    inputs, outputs = split_transcript(simulation.messages)
    root = TraceEvent(
        event_id=stable_id("event", sim_id, "conversation"),
        event_type=EventType.CONVERSATION,
        timestamp=simulation.start_time,
        context=context,
        run_id=root_id,
        elapsed_ms=simulation.duration_seconds * 1000,
        inputs={"messages": [chat_message(m) for m in inputs]},
        outputs={"messages": [chat_message(m) for m in outputs]},
    )
    events = [root]
    calls: dict[str, ToolCall] = {}
    for message in simulation.messages:
        calls.update((call.call_id, call) for call in message.tool_calls)
        if message.is_model_output:
            events.append(model_call_event(message, context, root_id))
        elif message.role is Role.TOOL:
            call = calls.get(message.tool_call_id)
            if call is None:
                raise ValueError(
                    f"simulation {sim_id}: tool result {message.tool_call_id} answers no tool call"
                )
            events.append(tool_call_event(message, call, context, root_id))
    feedback = reward_feedback(simulation.reward, simulation.termination_reason, context, root_id)
    return events, feedback
