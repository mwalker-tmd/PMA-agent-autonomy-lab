"""Send one small synthetic thread that shows which runs a thread evaluator reads.

Run with: uv run --env-file .env python -m agent_autonomy_lab.entrypoints.thread_probe

A traced conversation's root already holds its tool calls and results, so a
real thread cannot show whether a thread view reads the child spans. Here each
place has its own marker: the root's messages, and child spans whose content
the root does not hold. The evaluator's traces then show which markers the
judge received (docs/ARCHITECTURE.md, "Thread evaluator validation").
``--dry-run`` builds and checks the trace but sends nothing.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_autonomy_lab.adapters.langsmith import (
    LangSmithTraceSink,
    langsmith_client,
    shape_errors,
)
from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    Outcome,
    RunContext,
    TraceEvent,
    argument_hash,
    new_id,
    thread_id_for,
)
from agent_autonomy_lab.entrypoints.settings import langsmith_project
from agent_autonomy_lab.ports.trace_sink import InMemoryTraceSink, TraceSink

ROOT_MARKER = "PROBE-ROOT-MESSAGE"
CHILD_TOOL_MARKER = "PROBE-CHILD-TOOL"
CHILD_MODEL_MARKER = "PROBE-CHILD-MODEL"


def probe_events(simulation_id: str, start: datetime) -> list[TraceEvent]:
    """A root with one tool call in its messages, and children holding what it does not."""
    context = RunContext(
        simulation_id=simulation_id,
        thread_id=thread_id_for(simulation_id),
        task_id="thread-probe",
        autonomy_level=NO_GATE,
        experiment_class=ExperimentClass.CUSTOM_LAB_EXPERIMENT,
        agent_version="thread_probe",
    )
    root_id = new_id()

    def child(offset_s: int, event_type: EventType, **fields: Any) -> TraceEvent:
        return TraceEvent(
            event_id=new_id(),
            event_type=event_type,
            timestamp=start + timedelta(seconds=offset_s),
            context=context,
            run_id=new_id(),
            parent_run_id=root_id,
            outcome=Outcome.SUCCESS,
            **fields,
        )

    arguments = {"order_id": "#W0000000"}
    return [
        TraceEvent(
            event_id=new_id(),
            event_type=EventType.CONVERSATION,
            timestamp=start,
            context=context,
            run_id=root_id,
            outcome=Outcome.SUCCESS,
            elapsed_ms=4000,
            inputs={
                "messages": [
                    {"role": "assistant", "content": "Hi! How can I help you today?"},
                    {"role": "user", "content": "Where is order #W0000000?"},
                ]
            },
            outputs={
                "messages": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_root",
                                "type": "function",
                                "function": {
                                    "name": "get_order_details",
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_root", "content": ROOT_MARKER},
                    {"role": "assistant", "content": "Your order is on its way."},
                ]
            },
        ),
        child(
            1,
            EventType.MODEL_CALL,
            turn_index=2,
            outputs={"messages": [{"role": "assistant", "content": CHILD_MODEL_MARKER}]},
        ),
        child(
            2,
            EventType.TOOL_CALL,
            turn_index=3,
            tool_name="child_only_probe",
            argument_hash=argument_hash(arguments),
            inputs=arguments,
            outputs={"content": CHILD_TOOL_MARKER},
        ),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="check the trace, send nothing")
    args = parser.parse_args(argv)

    sink: TraceSink
    project = langsmith_project()
    if args.dry_run:
        sink = InMemoryTraceSink()
    else:
        if not os.environ.get("LANGSMITH_API_KEY"):
            print("LANGSMITH_API_KEY is not set; copy .env.example to .env.", file=sys.stderr)
            return 1
        # Synchronous sends, so a run LangSmith rejects raises instead of only being logged.
        sink = LangSmithTraceSink(langsmith_client(auto_batch_tracing=False), project)

    events = probe_events(f"thread-probe-{new_id()}", datetime.now(UTC))
    if errors := shape_errors(events, []):
        print("\n".join(errors), file=sys.stderr)
        return 1
    for event in events:
        sink.record(event)
    sink.flush()

    print(f"project:       {project}{' (dry run, nothing sent)' if args.dry_run else ''}")
    print(f"simulation_id: {events[0].context.simulation_id}")
    print(f"thread_id:     {events[0].context.thread_id}")
    print(f"root_run_id:   {events[0].run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
