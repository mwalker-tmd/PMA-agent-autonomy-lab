"""A lab agent that records every turn of an unmodified ``LLMAgent`` (hook A).

τ-bench runs custom agents through its registry: a factory registered with
``registry.register_agent_factory`` builds the agent for each simulation
(docs/BASELINE.md, "LangSmith hook points"). ``TracedAgentFactory`` builds
upstream's own ``LLMAgent`` with upstream's ``create_llm_agent`` and wraps it
in a ``TracedAgent``, which forwards every call unchanged and records what
passed through in a ``TurnRecorder``. The wrapper adds no prompt, tool, or
model argument, so the model sees exactly what the baseline agent sends;
``fingerprint()`` hashes what the agent sends so a run can show it.

The recorder keeps lab records, not τ-bench objects, so the trace is built
from data the core understands. It does not know the simulation ID: τ-bench
assigns it inside the runner and passes it to no agent, so the caller links
the recording to the returned ``SimulationRun`` afterwards.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from tau2.agent.base_agent import HalfDuplexAgent
from tau2.agent.llm_agent import LLMAgent, create_llm_agent
from tau2.data_model.message import Message as Tau2Message
from tau2.data_model.message import MultiToolMessage, SystemMessage
from tau2.environment.toolkit import ToolKitBase

from agent_autonomy_lab.adapters.tau2.results_reader import message_from_dict
from agent_autonomy_lab.domain.agent_turns import AgentFingerprint, AgentTurn, LiveRecording
from agent_autonomy_lab.domain.events import (
    model_args_version,
    prompt_version,
    tool_schema_version,
)
from agent_autonomy_lab.domain.simulations import Message

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def fingerprint(agent: LLMAgent, system_messages: Sequence[SystemMessage]) -> AgentFingerprint:
    """Hash what ``agent`` sends its model besides the conversation.

    ``system_messages`` are the ones in the agent's state, which ``LLMAgent``
    puts before the conversation on every call.
    """
    return AgentFingerprint(
        model_id=agent.llm,
        prompt_version=prompt_version("\n\n".join(m.content or "" for m in system_messages)),
        tool_schema_version=tool_schema_version([tool.openai_schema for tool in agent.tools]),
        model_args_version=model_args_version(agent.llm_args),
        model_args=agent.llm_args,
    )


class TurnRecorder:
    """Collects one simulation's transcript and agent turns as lab records.

    τ-bench stamps messages with local time and no zone, and numbers them
    only when the run ends, so the recorder numbers each message by its
    position in the transcript and reads its time in this machine's zone.
    """

    def __init__(self, agent_version: str, clock: Clock = _utc_now) -> None:
        self.agent_version = agent_version
        self._clock = clock
        self._local_tz = datetime.now().astimezone().tzinfo
        self.transcript: list[Message] = []
        self.turns: list[AgentTurn] = []
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None
        self.overhead_ms = 0.0

    def now(self) -> datetime:
        return self._clock()

    def start(self, history: Sequence[Tau2Message]) -> None:
        """Begin a simulation; ``history`` is what the agent starts with (the opener)."""
        self.started_at = self.now()
        self.transcript = []
        self._append(history)

    def add_turn(
        self,
        incoming: Tau2Message | None,
        started_at: datetime,
        elapsed_ms: float,
        sent: AgentFingerprint,
        reply: Tau2Message | None = None,
        error: BaseException | None = None,
    ) -> None:
        inputs = self._append(_unpack(incoming))
        output = self._append([reply])[0] if reply is not None else None
        self.turns.append(
            AgentTurn(
                turn_index=output.turn_index if output else len(self.transcript),
                started_at=started_at,
                elapsed_ms=elapsed_ms,
                fingerprint=sent,
                inputs=tuple(inputs),
                output=output,
                error_class=type(error).__name__ if error is not None else None,
                error_message=(str(error) or None) if error is not None else None,
            )
        )

    def finish(self, last: Tau2Message | None) -> None:
        """End the simulation; ``last`` is a final message the agent never answered."""
        if self.ended_at is not None:
            return
        self._append(_unpack(last))
        self.ended_at = self.now()

    def recording(self) -> LiveRecording:
        if self.started_at is None:
            raise ValueError("the agent never started a simulation")
        return LiveRecording(
            agent_version=self.agent_version,
            started_at=self.started_at,
            ended_at=self.ended_at or self.now(),
            transcript=tuple(self.transcript),
            turns=tuple(self.turns),
            overhead_ms=self.overhead_ms,
        )

    def _append(self, messages: Sequence[Tau2Message]) -> list[Message]:
        """Convert and append ``messages`` one at a time, so each gets its own position."""
        added = []
        for message in messages:
            added.append(self._message(message))
            self.transcript.append(added[-1])
        return added

    def _message(self, message: Tau2Message) -> Message:
        raw = {
            "role": message.role,
            "turn_idx": len(self.transcript),
            "timestamp": message.timestamp or self.now().isoformat(),
            "content": getattr(message, "content", None),
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in getattr(message, "tool_calls", None) or []
            ],
            "id": getattr(message, "id", None),
            "error": getattr(message, "error", False),
            "execution_status": getattr(message, "execution_status", None),
            "usage": getattr(message, "usage", None),
            "cost": getattr(message, "cost", None),
            "generation_time_seconds": getattr(message, "generation_time_seconds", None),
        }
        return message_from_dict(raw, self._local_tz)


def _unpack(message: Tau2Message | None) -> list[Tau2Message]:
    if message is None:
        return []
    if isinstance(message, MultiToolMessage):
        return list(message.tool_messages)
    return [message]


class TracedAgent(HalfDuplexAgent):
    """Forwards every call to ``inner`` unchanged and records each turn.

    Only ``generate_next_message`` calls the model. The wrapper times that
    call and counts the rest of its own work as recording overhead.
    """

    def __init__(self, inner: LLMAgent, recorder: TurnRecorder) -> None:
        super().__init__(tools=inner.tools, domain_policy=inner.domain_policy)
        self.inner = inner
        self.recorder = recorder

    @property
    def additional_stop_tool_names(self) -> frozenset[str]:
        return self.inner.additional_stop_tool_names

    def set_seed(self, seed: int) -> None:
        self.inner.set_seed(seed)

    def is_stop(self, message: Any) -> bool:
        return self.inner.is_stop(message)

    def get_init_state(self, message_history: list[Tau2Message] | None = None) -> Any:
        state = self.inner.get_init_state(message_history)
        self._timed(lambda: self.recorder.start(message_history or []))
        return state

    def generate_next_message(self, message: Any, state: Any) -> tuple[Any, Any]:
        entered = time.perf_counter()
        sent = fingerprint(self.inner, state.system_messages)
        started_at = self.recorder.now()
        call_start = time.perf_counter()
        try:
            reply, state = self.inner.generate_next_message(message, state)
        except Exception as error:
            call_end = time.perf_counter()
            self.recorder.add_turn(
                message, started_at, (call_end - call_start) * 1000, sent, error=error
            )
            self._add_overhead(entered, call_start, call_end)
            raise
        call_end = time.perf_counter()
        self.recorder.add_turn(message, started_at, (call_end - call_start) * 1000, sent, reply)
        self._add_overhead(entered, call_start, call_end)
        return reply, state

    def stop(self, message: Any = None, state: Any = None) -> None:
        self.inner.stop(message, state)
        self._timed(lambda: self.recorder.finish(message))

    def _timed(self, work: Callable[[], None]) -> None:
        start = time.perf_counter()
        work()
        self.recorder.overhead_ms += (time.perf_counter() - start) * 1000

    def _add_overhead(self, entered: float, call_start: float, call_end: float) -> None:
        own = (call_start - entered) + (time.perf_counter() - call_end)
        self.recorder.overhead_ms += own * 1000


def live_toolkit(tools: Sequence[Any]) -> ToolKitBase | None:
    """The toolkit, and so the live database, that an agent's tools act on.

    τ-bench gives the agent ``Tool`` objects whose functions are bound
    methods of the run's live environment toolkit (docs/BASELINE.md,
    "Authorization gate boundary"), so the toolkit is the functions' common
    ``__self__``. Reading it relies on ``Tool._func``, an internal attribute;
    docs/ARCHITECTURE.md lists it among the things to recheck on upgrade.
    Returns ``None`` when there are no tools.
    """
    if not tools:
        return None
    owners = [getattr(getattr(tool, "_func", None), "__self__", None) for tool in tools]
    kit = owners[0]
    if not isinstance(kit, ToolKitBase) or any(owner is not kit for owner in owners):
        raise ValueError("the agent's tools are not the bound methods of one toolkit")
    return kit


class TracedAgentFactory:
    """An agent factory for ``registry.register_agent_factory``.

    Each call builds upstream's ``LLMAgent`` exactly as the registered
    ``llm_agent`` factory does and wraps it with a fresh recorder, kept in
    ``recorders`` in build order. ``toolkits`` keeps, in the same order, the
    live toolkit each agent's tools act on, so a run's final database can be
    read after it ends.
    """

    def __init__(self, agent_version: str, clock: Clock = _utc_now) -> None:
        self.agent_version = agent_version
        self._clock = clock
        self.recorders: list[TurnRecorder] = []
        self.toolkits: list[ToolKitBase | None] = []

    def __call__(self, tools: list[Any], domain_policy: str, **kwargs: Any) -> TracedAgent:
        inner = create_llm_agent(tools, domain_policy, **kwargs)
        recorder = TurnRecorder(self.agent_version, self._clock)
        self.recorders.append(recorder)
        self.toolkits.append(live_toolkit(tools))
        return TracedAgent(inner, recorder)


class UntracedAgentFactory:
    """Builds upstream's ``LLMAgent`` and returns it unwrapped: tracing off.

    The agent is exactly what the registered ``llm_agent`` factory builds;
    nothing records its turns. Only ``toolkits`` is kept, as in
    ``TracedAgentFactory``, so the run's final database can be read.
    """

    def __init__(self, agent_version: str) -> None:
        self.agent_version = agent_version
        self.toolkits: list[ToolKitBase | None] = []

    def __call__(self, tools: list[Any], domain_policy: str, **kwargs: Any) -> LLMAgent:
        self.toolkits.append(live_toolkit(tools))
        return create_llm_agent(tools, domain_policy, **kwargs)
