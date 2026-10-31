"""A simulation's tool calls in order, and what its record establishes about each.

The record links a call to its result only through a shared ID, so it can fail to
say what a call did. ``ToolCallRecord.problem`` names that failure. The replay
withholds state on it, and the evaluators report ``INSUFFICIENT_EVIDENCE``
unless the attempt alone proves a violation.

The tool names are the retail toolkit's at τ-bench ``0c69df4``
(docs/BASELINE.md, "Tool inventory"). A test compares them with the toolkit.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from agent_autonomy_lab.domain.simulations import ExecutionStatus, Message, Role, ToolCall

AUTHENTICATION_TOOLS = frozenset({"find_user_id_by_email", "find_user_id_by_name_zip"})
WRITE_TOOLS = frozenset(
    {
        "cancel_pending_order",
        "exchange_delivered_order_items",
        "modify_pending_order_address",
        "modify_pending_order_items",
        "modify_pending_order_payment",
        "modify_user_address",
        "return_delivered_order_items",
    }
)
PROTECTED_READS = frozenset({"get_user_details", "get_order_details"})
PROTECTED_OPERATIONS = PROTECTED_READS | WRITE_TOOLS
UNPROTECTED_TOOLS = frozenset(
    {
        "calculate",
        "get_item_details",
        "get_product_details",
        "list_all_product_types",
        "transfer_to_human_agents",
    }
)
KNOWN_TOOLS = AUTHENTICATION_TOOLS | PROTECTED_OPERATIONS | UNPROTECTED_TOOLS


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call, the message that holds it, and every result for its ID."""

    call: ToolCall
    message: Message
    message_index: int
    position: int
    results: tuple[Message, ...]
    problem: str | None

    @property
    def result(self) -> Message | None:
        """The call's one result, or ``None`` if the record does not establish one."""
        return self.results[0] if len(self.results) == 1 and not self.problem else None


def tool_timeline(messages: Sequence[Message]) -> tuple[ToolCallRecord, ...]:
    """Each tool call in order, with every result in the tool messages right after it.

    More than one result means the record conflicts with itself, so none is chosen.
    """
    call_id_uses = Counter(call.call_id for m in messages for call in m.tool_calls)
    records = []
    for index, message in enumerate(messages):
        if not message.tool_calls:
            continue
        block = []
        for later in messages[index + 1 :]:
            if later.role is not Role.TOOL:
                break
            block.append(later)
        for position, call in enumerate(message.tool_calls):
            results = tuple(r for r in block if r.tool_call_id == call.call_id)
            records.append(
                ToolCallRecord(
                    call=call,
                    message=message,
                    message_index=index,
                    position=position,
                    results=results,
                    problem=_record_problem(call, results, call_id_uses),
                )
            )
    return tuple(records)


def results_without_a_call(messages: Sequence[Message]) -> tuple[tuple[int, str], ...]:
    """Index and ID of tool results that answer no call of the assistant message before them."""
    orphans = []
    open_call_ids: set[str] = set()
    for index, message in enumerate(messages):
        if message.role is Role.TOOL:
            if message.tool_call_id not in open_call_ids:
                orphans.append((index, message.tool_call_id))
        else:
            open_call_ids = {call.call_id for call in message.tool_calls}
    return tuple(orphans)


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
