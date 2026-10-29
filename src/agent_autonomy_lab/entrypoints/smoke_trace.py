"""Send one synthetic smoke trace to the configured LangSmith project.

Run with: uv run --env-file .env python -m agent_autonomy_lab.entrypoints.smoke_trace
"""

import os
import sys

from langsmith import get_current_run_tree, traceable, tracing_context

from agent_autonomy_lab.adapters.langsmith import langsmith_client
from agent_autonomy_lab.entrypoints.settings import langsmith_project


@traceable(name="smoke_trace", run_type="chain")
def _smoke(message: str) -> dict:
    run = get_current_run_tree()
    return {"echo": message, "trace_id": str(run.trace_id)}


def main() -> int:
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY is not set; copy .env.example to .env.", file=sys.stderr)
        return 1
    project = langsmith_project()
    client = langsmith_client()
    with tracing_context(
        enabled=True,
        project_name=project,
        client=client,
        tags=["smoke"],
        metadata={"traffic_type": "synthetic"},
    ):
        result = _smoke("Synthetic smoke trace from the agent autonomy lab.")
    client.flush()
    print(f"project:  {project}")
    print(f"trace_id: {result['trace_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
