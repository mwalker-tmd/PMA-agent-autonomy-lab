"""Trace a live run recorded by the lab agent wrapper (hook A, docs/BASELINE.md).

The wrapper records what the agent saw and did but cannot know the
simulation ID, which τ-bench assigns inside its runner. Once the run returns,
``live_trace`` combines the recording with the returned simulation: the
thread is ``thread_id_for(simulation_id)``, as in the saved-run export, and
the benchmark reward becomes feedback on the root. Each agent turn's span
carries the fingerprint versions that turn sent; the run context carries
only the versions every turn shares.

When the run raised, τ-bench returns no simulation and exposes no ID, so
the caller passes an ``UnfinishedSimulation`` with a lab-assigned
``unfinished-`` ID instead. The partial conversation is still traced: its
root is an error span carrying the exception, and there is no reward.

A run the lab injected a tool failure into passes the ``InjectedFault``: every
span is labeled ``fault_injected``, and the tool span of the failed call
carries the fault's ``error_class``.

The trace has the export's shape, with three differences: the root and each
agent turn are timed by the wrapper; each agent ``model_call`` holds the
messages the turn answered and the model arguments sent, and records an
error outcome when the call raised; and its IDs include ``"live"``, so they
can never collide with an export of the same simulation.
"""

import time
from collections.abc import Callable, Sequence
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
from agent_autonomy_lab.domain.agent_turns import (
    AgentTurn,
    InjectedFault,
    LiveRecording,
    UnfinishedSimulation,
)
from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Feedback,
    Outcome,
    RunContext,
    TraceEvent,
    stable_id,
    stable_time_id,
    thread_id_for,
)
from agent_autonomy_lab.domain.simulations import Role, SavedSimulation, ToolCall
from agent_autonomy_lab.ports.trace_sink import TraceSink

LIVE = ("live",)


@dataclass(frozen=True)
class LiveTrace:
    """What was recorded for one live simulation, and how long tracing took after the run."""

    simulation_id: str
    thread_id: str
    root_run_id: UUID
    spans: int
    feedback: int
    build_ms: float
    flush_ms: float
    fault_run_id: UUID | None = None


def live_trace(
    recording: LiveRecording,
    simulation: SavedSimulation | UnfinishedSimulation,
    user_model_id: str | None = None,
    policy_version: str | None = None,
    experiment_class: ExperimentClass = ExperimentClass.CUSTOM_LAB_EXPERIMENT,
    fault: InjectedFault | None = None,
) -> tuple[list[TraceEvent], list[Feedback]]:
    """Return the trace events, root first, and the reward feedback.

    Raises ``ValueError`` when the transcript has fewer than ``MIN_MESSAGES``
    messages, as the export does: there is no conversation to show.
    """
    if len(recording.transcript) < MIN_MESSAGES:
        raise ValueError(
            f"simulation {simulation.simulation_id}: {len(recording.transcript)} message(s) "
            "cannot form a conversation"
        )
    context = RunContext(
        simulation_id=simulation.simulation_id,
        thread_id=thread_id_for(simulation.simulation_id),
        task_id=simulation.task_id,
        autonomy_level=NO_GATE,
        experiment_class=experiment_class,
        fault_injected=fault is not None,
        agent_version=recording.agent_version,
        prompt_version=_shared(recording, "prompt_version"),
        model_id=_shared(recording, "model_id"),
        user_model_id=user_model_id,
        tool_schema_version=_shared(recording, "tool_schema_version"),
        policy_version=policy_version,
    )
    sim_id = simulation.simulation_id
    root_id = stable_time_id(epoch_ms(recording.started_at), "run", sim_id, *LIVE, "conversation")
    inputs, outputs = split_transcript(recording.transcript)
    unfinished = isinstance(simulation, UnfinishedSimulation)
    root = TraceEvent(
        event_id=stable_id("event", sim_id, *LIVE, "conversation"),
        event_type=EventType.CONVERSATION,
        timestamp=recording.started_at,
        context=context,
        run_id=root_id,
        elapsed_ms=recording.elapsed_ms,
        outcome=Outcome.ERROR if unfinished else None,
        error_class=simulation.error_class if unfinished else None,
        error_message=simulation.error_message if unfinished else None,
        inputs={"messages": [chat_message(m) for m in inputs]},
        outputs={"messages": [chat_message(m) for m in outputs]},
    )
    events = [root]
    events += [_agent_turn(turn, context, root_id) for turn in recording.turns]
    calls: dict[str, ToolCall] = {}
    for message in recording.transcript:
        calls.update((call.call_id, call) for call in message.tool_calls)
        if message.role is Role.USER and message.is_model_output:
            events.append(model_call_event(message, context, root_id, LIVE))
        elif message.role is Role.TOOL:
            call = calls.get(message.tool_call_id)
            if call is None:
                raise ValueError(
                    f"simulation {sim_id}: tool result {message.tool_call_id} answers no tool call"
                )
            failed = fault is not None and fault.call_id == call.call_id
            error_class = fault.error_class if failed else None
            events.append(tool_call_event(message, call, context, root_id, LIVE, error_class))
    feedback = reward_feedback(
        simulation.reward, simulation.termination_reason, context, root_id, LIVE
    )
    return events, feedback


def record_live_run(
    recording: LiveRecording,
    simulation: SavedSimulation | UnfinishedSimulation,
    sink: TraceSink,
    user_model_id: str | None = None,
    policy_version: str | None = None,
    timer: Callable[[], float] = time.perf_counter,
    fault: InjectedFault | None = None,
    extra_feedback: Sequence[Feedback] = (),
) -> LiveTrace:
    """Record the live trace in ``sink`` and flush it, timing both steps.

    ``extra_feedback`` goes with the reward, such as evaluator results on these spans.
    """
    start = timer()
    events, feedback = live_trace(recording, simulation, user_model_id, policy_version, fault=fault)
    for event in events:
        sink.record(event)
    for item in (*feedback, *extra_feedback):
        sink.record_feedback(item)
    built = timer()
    sink.flush()
    flushed = timer()
    return LiveTrace(
        simulation_id=simulation.simulation_id,
        thread_id=events[0].context.thread_id,
        root_run_id=events[0].run_id,
        spans=len(events),
        feedback=len(feedback),
        build_ms=(built - start) * 1000,
        flush_ms=(flushed - built) * 1000,
        fault_run_id=_classified_tool_span(events),
    )


def _classified_tool_span(events: list[TraceEvent]) -> UUID | None:
    """The tool span carrying an injected fault's class; a live run has at most one."""
    spans = (e.run_id for e in events if e.event_type is EventType.TOOL_CALL and e.error_class)
    return next(spans, None)


def _shared(recording: LiveRecording, name: str) -> str | None:
    """A fingerprint field every turn sent with the same value, else ``None``.

    The run context describes the whole thread, so a field the turns
    disagree on is left out of it; each turn's span keeps its own value.
    """
    values = {getattr(turn.fingerprint, name) for turn in recording.turns}
    return values.pop() if len(values) == 1 else None


def _agent_turn(turn: AgentTurn, context: RunContext, root_id: UUID) -> TraceEvent:
    """One agent call, timed by the wrapper, with the messages it answered and its reply."""
    sim_id = context.simulation_id
    index = str(turn.turn_index)
    output = turn.output
    failed = output is None
    return TraceEvent(
        event_id=stable_id("event", sim_id, *LIVE, "model_call", index),
        event_type=EventType.MODEL_CALL,
        timestamp=turn.started_at,
        context=context,
        run_id=stable_time_id(epoch_ms(turn.started_at), "run", sim_id, *LIVE, "model_call", index),
        parent_run_id=root_id,
        turn_index=turn.turn_index,
        outcome=Outcome.ERROR if failed else Outcome.SUCCESS,
        error_class=turn.error_class,
        error_message=turn.error_message,
        elapsed_ms=turn.elapsed_ms,
        tokens=output.tokens if output else None,
        cost=output.cost if output else None,
        inputs={
            "messages": [chat_message(m) for m in turn.inputs],
            "model": turn.fingerprint.model_id,
            "model_args": turn.fingerprint.model_args,
            "prompt_version": turn.fingerprint.prompt_version,
            "tool_schema_version": turn.fingerprint.tool_schema_version,
            "model_args_version": turn.fingerprint.model_args_version,
        },
        outputs=None if failed else {"messages": [chat_message(output)]},
    )
