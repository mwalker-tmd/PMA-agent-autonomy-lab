import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_autonomy_lab.domain.events import (
    NO_GATE,
    EventType,
    ExperimentClass,
    RunContext,
    TraceEvent,
    new_id,
    thread_id_for,
)

T0 = datetime(2026, 10, 27, 6, 25, 9, tzinfo=UTC)
DENVER = ZoneInfo("America/Denver")

# A synthetic results.json in τ-bench's format at the pinned commit: one
# simulation with a failed tool call. Sentinel strings mark content that must
# never be exported (raw_data, the task's hidden user instructions) and the
# answer key, which may reach only feedback (an NL assertion, and a reference
# action the agent never took).
SAMPLE_RUN = Path(__file__).parent / "fixtures" / "sample-run" / "results.json"
SAMPLE_SIMULATION_ID = "00000000-0000-4000-8000-000000000001"
NOT_EXPORTED = (
    "RAWDATA-SENTINEL",
    "HIDDEN-INSTRUCTIONS-SENTINEL",
    "HIDDEN-KNOWN-INFO-SENTINEL",
    "HIDDEN-UNKNOWN-INFO-SENTINEL",
    "HIDDEN-TASK-INSTRUCTIONS-SENTINEL",
    "raw_data",
    "user_scenario",
    "evaluation_criteria",
)
ANSWER_KEY = ("NL-ASSERTION-SENTINEL", "REFERENCE-ACTION-SENTINEL")


def make_context(simulation_id: str = "sim-00023", **overrides) -> RunContext:
    fields = {
        "simulation_id": simulation_id,
        "task_id": "0",
        "autonomy_level": NO_GATE,
        "experiment_class": ExperimentClass.CUSTOM_LAB_EXPERIMENT,
    }
    if "thread_id" not in overrides:
        fields["thread_id"] = thread_id_for(simulation_id)
    return RunContext(**(fields | overrides))


def make_event(event_type: EventType, context: RunContext | None = None, **overrides):
    fields = {
        "event_id": new_id(),
        "event_type": event_type,
        "timestamp": T0,
        "context": context or make_context(),
        "run_id": new_id(),
    }
    if event_type in (EventType.MODEL_CALL, EventType.TOOL_CALL):
        fields["parent_run_id"] = new_id()
    if event_type in (EventType.TOOL_CALL, EventType.GATE):
        fields["tool_name"] = "get_order_details"
    return TraceEvent(**(fields | overrides))


@pytest.fixture(autouse=True)
def no_langsmith_settings(monkeypatch):
    """Hide LangSmith settings from every test.

    Importing τ-bench runs ``load_dotenv()``, which copies the developer's
    ``.env`` (API key included) into the process environment, and the
    LangSmith SDK reads that environment. No test may see real credentials.
    """
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGCHAIN_")):
            monkeypatch.delenv(name)


@pytest.fixture
def context() -> RunContext:
    return make_context()
