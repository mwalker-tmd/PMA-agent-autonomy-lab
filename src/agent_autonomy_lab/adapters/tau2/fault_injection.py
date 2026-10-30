"""A one-shot, recoverable tool failure for one live run (docs/ARCHITECTURE.md).

τ-bench recorded no tool error in the baseline, so a failed tool call has to
be provoked to show that it can be inspected. ``OneShotToolFault`` wraps
``get_response`` on the one ``Environment`` object a run's orchestrator holds,
without changing τ-bench: the first call to the configured tool gets an error
result, as if the tool had raised, and every other call passes through.

Only read tools can be configured. Evaluation replays a trajectory in fresh
environments and skips non-mutating calls, so the final database and the
reward replay never see the failed call. The wrapper sets an attribute on one
instance and is never registered, so ``retail`` still builds unwrapped
environments for the evaluator and every other run.

The agent sees only ``FAULT_CONTENT``, worded as a real outage: nothing in it
says the failure was deliberate, so what the agent does next is what it would
do for a real one. The classification goes to the trace, never to the agent.
"""

from tau2.data_model.message import ToolCall, ToolMessage
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolType

from agent_autonomy_lab.domain.agent_turns import INJECTED_FAULT, InjectedFault

FAULT_CONTENT = "Error: Service temporarily unavailable"


class OneShotToolFault:
    """Fails the first call to ``tool_name``; ``injected`` records it once it has fired."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.injected: InjectedFault | None = None

    def install(self, environment: Environment) -> None:
        """Wrap this ``environment``'s ``get_response``.

        Raises ``ValueError`` unless the configured tool is a read tool of
        ``environment``: failing a write would change what evaluation replays.
        """
        tools = environment.tools
        if tools is None or not tools.has_tool(self.tool_name):
            raise ValueError(f"{self.tool_name!r} is not a tool of this environment")
        if tools.tool_type(self.tool_name) is not ToolType.READ or tools.tool_mutates_state(
            self.tool_name
        ):
            raise ValueError(f"{self.tool_name!r} is not a read tool; only reads can be failed")
        passthrough = environment.get_response

        def get_response(message: ToolCall) -> ToolMessage:
            if self.injected is not None or message.name != self.tool_name:
                return passthrough(message)
            self.injected = _injected(message)
            return ToolMessage(
                id=message.id,
                content=FAULT_CONTENT,
                requestor=message.requestor,
                role="tool",
                error=True,
            )

        environment.get_response = get_response


def _injected(message: ToolCall) -> InjectedFault:
    return InjectedFault(
        call_id=message.id,
        tool_name=message.name,
        arguments=message.arguments,
        error_class=INJECTED_FAULT,
        error_message=FAULT_CONTENT,
    )
