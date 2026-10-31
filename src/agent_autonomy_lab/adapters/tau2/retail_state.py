"""Replay a saved simulation's writes offline to get the retail state at each one.

τ-bench's live run keeps no database, so this rebuilds it: a fresh environment
from ``db.json``, then the run's writes in order through ``get_response``, the
call a live run makes. No model is called. The environment is built directly
and never registered, so ``retail`` still means upstream's environment for
every run and for τ-bench's evaluator, which this does not call.

A write that returned an error is replayed too, as τ-bench's own replay does
(``Environment.set_state``), because a tool can change state before it raises.
No retail task sets an ``initial_state`` at the pinned commit, so ``db.json`` is
every run's starting point.

The replay checks itself against the record: each write's result and each
successful ``get_order_details`` status read must match. A mismatch, a write
with no result, or a record that contradicts itself about what ran
(``_record_problem``) is a divergence, and later state is withheld.
"""

import json
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from tau2.data_model.message import ToolCall as Tau2ToolCall
from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail.environment import get_environment
from tau2.domains.retail.utils import RETAIL_DB_PATH
from tau2.environment.environment import Environment

from agent_autonomy_lab.domain.events import canonical_json
from agent_autonomy_lab.domain.retail_state import (
    ReplayDivergence,
    RetailState,
    RetailStateTimeline,
    WriteAttempt,
    WriteOutcome,
)
from agent_autonomy_lab.domain.simulations import (
    ExecutionStatus,
    Message,
    Role,
    SavedSimulation,
    ToolCall,
)

_STATUS_READ = "get_order_details"


class ReplayedRetailState:
    """``RetailStateSource`` over τ-bench's retail tools and ``db.json``."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db = RetailDB.load(str(db_path or RETAIL_DB_PATH))

    def timeline(self, simulation: SavedSimulation) -> RetailStateTimeline:
        environment = get_environment(db=self._db)  # takes its own deep copy
        writes: list[WriteAttempt] = []
        divergences: list[ReplayDivergence] = []
        status_reads = 0
        messages = simulation.messages
        call_id_uses = Counter(call.call_id for m in messages for call in m.tool_calls)
        for orphan in _results_without_a_call(messages):
            # A lost call could be a write at any earlier point, so no state can be vouched for.
            divergences.append(
                ReplayDivergence(orphan, "unknown", "a tool result has no matching call")
            )

        def diverged(call: ToolCall, reason: str) -> None:
            divergences.append(ReplayDivergence(call.call_id, call.name, reason))

        for call, results in _calls_with_results(messages):
            problem = _record_problem(call, results, call_id_uses)
            result = results[0] if len(results) == 1 and not problem else None
            if _mutates_state(environment, call.name):
                writes.append(
                    WriteAttempt(
                        call.call_id,
                        call.name,
                        _outcome(results, problem),
                        None if divergences else _snapshot(environment),
                    )
                )
                if problem:
                    diverged(call, problem)
                elif not results:
                    diverged(call, "the write has no result, so whether it ran is unknown")
                elif result.execution_status is ExecutionStatus.EXECUTED:
                    reason = _replay_write(environment, call, result)
                    if reason:
                        diverged(call, reason)
            elif problem:
                diverged(call, problem)
            elif call.name == _STATUS_READ and _succeeded(result):
                status_reads += 1
                reason = _status_mismatch(environment, call, result)
                if reason:
                    diverged(call, reason)

        return RetailStateTimeline(
            simulation_id=simulation.simulation_id,
            writes=tuple(writes),
            final=None if divergences else _snapshot(environment),
            replayed_hash=environment.get_db_hash(),
            status_reads_checked=status_reads,
            divergences=tuple(divergences),
        )


def _calls_with_results(
    messages: Sequence[Message],
) -> Iterator[tuple[ToolCall, tuple[Message, ...]]]:
    """Each tool call in order, with every result for its ID in the tool messages right after it.

    More than one means the record conflicts with itself, so no result is chosen.
    """
    for index, message in enumerate(messages):
        if not message.tool_calls:
            continue
        block = []
        for later in messages[index + 1 :]:
            if later.role is not Role.TOOL:
                break
            block.append(later)
        for call in message.tool_calls:
            yield call, tuple(r for r in block if r.tool_call_id == call.call_id)


def _results_without_a_call(messages: Sequence[Message]) -> list[str]:
    """IDs of tool results that do not answer a call of the assistant message before them."""
    orphans = []
    open_call_ids: set[str] = set()
    for message in messages:
        if message.role is Role.TOOL:
            if message.tool_call_id not in open_call_ids:
                orphans.append(message.tool_call_id)
        else:
            open_call_ids = {call.call_id for call in message.tool_calls}
    return orphans


def _record_problem(
    call: ToolCall, results: Sequence[Message], call_id_uses: Counter[str]
) -> str | None:
    """Say how the record fails to establish what this call did, if it does."""
    if call_id_uses[call.call_id] > 1:
        return "the call ID is used more than once, so its result cannot be attributed"
    if len(results) > 1:
        return "the call has several results, so what it did is unknown"
    if results:
        status, error = results[0].execution_status, results[0].error
        if status is None:
            return "the result has no execution status, so whether it ran is unknown"
        if status is ExecutionStatus.REJECTED and not error:
            return "the result is marked rejected with no error, which contradicts itself"
    return None


def _mutates_state(environment: Environment, tool_name: str) -> bool:
    tools = environment.tools
    return tools.has_tool(tool_name) and tools.tool_mutates_state(tool_name)


def _outcome(results: Sequence[Message], problem: str | None) -> WriteOutcome:
    if problem:
        return WriteOutcome.AMBIGUOUS
    if not results:
        return WriteOutcome.NO_RESULT
    result = results[0]
    if result.execution_status is ExecutionStatus.REJECTED:
        return WriteOutcome.REJECTED
    return WriteOutcome.FAILED if result.error else WriteOutcome.EXECUTED


def _succeeded(result: Message | None) -> bool:
    return (
        result is not None
        and not result.error
        and result.execution_status is ExecutionStatus.EXECUTED
    )


def _snapshot(environment: Environment) -> RetailState:
    data = environment.tools.db.model_dump(mode="json")
    return RetailState(
        db_hash=environment.get_db_hash(),
        orders=data["orders"],
        users=data["users"],
        products=data["products"],
    )


def _replay_write(environment: Environment, call: ToolCall, recorded: Message) -> str | None:
    """Run the write; say how its result differs from the recorded one, if it does."""
    replayed = environment.get_response(
        Tau2ToolCall(id=call.call_id, name=call.name, arguments=dict(call.arguments))
    )
    if replayed.error != recorded.error:
        return f"the replay's error flag is {replayed.error}, the record's {recorded.error}"
    if not _same_json(replayed.content, recorded.content):
        return "the replay's result differs from the recorded one"
    return None


def _status_mismatch(environment: Environment, call: ToolCall, recorded: Message) -> str | None:
    """Compare a successful status read with the replayed order; say how they differ."""
    read = _parsed(recorded.content)
    read_status = read.get("status") if isinstance(read, dict) else None
    order = environment.tools.db.orders.get(call.arguments.get("order_id"))
    if order is None:
        return "the agent read an order the replayed database does not have"
    if read_status != order.status:
        return f"the agent read status {read_status!r}, the replay has {order.status!r}"
    return None


def _same_json(first: str | None, second: str | None) -> bool:
    """Compare as canonical JSON: Python's ``==`` would make ``false``, ``0``, and ``0.0`` equal.

    Content JSON cannot carry, such as a NaN, proves nothing, so it does not match.
    """
    try:
        return canonical_json(_parsed(first)) == canonical_json(_parsed(second))
    except (TypeError, ValueError):
        return False


def _parsed(content: str | None) -> Any:
    """JSON content as data, so key order and spacing do not count as a difference."""
    try:
        return json.loads(content) if content is not None else None
    except json.JSONDecodeError:
        return content
