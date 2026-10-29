"""Read a saved τ-bench run (``results.json``) into the lab's simulation records.

The file is parsed as JSON with the field names documented in
docs/BASELINE.md ("Result file structure") at the pinned commit, so reading a
run does not import τ-bench or need its data directory. Only the fields the
lab exports are read: ``raw_data`` (the full provider response) and the
task's hidden user instructions are never copied. The reward checks are kept
for feedback.

τ-bench writes local time with no zone, so the caller names the zone of the
machine that ran the simulation.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_autonomy_lab.domain.events import TokenUsage, to_utc
from agent_autonomy_lab.domain.simulations import (
    Message,
    Reward,
    SavedRun,
    SavedSimulation,
    ToolCall,
)

# reward_info fields kept as feedback detail; reward, basis, and breakdown are read separately.
_REWARD_DETAILS = ("db_check", "nl_assertions", "action_checks", "env_assertions")


def results_path(run: str, data_dir: str | None = None) -> Path:
    """Return the ``results.json`` for ``run``: a path to the file or its directory,
    or a ``--save-to`` name under ``$TAU2_DATA_DIR/simulations``."""
    candidate = Path(run).expanduser()
    if candidate.is_file():
        return candidate
    if (candidate / "results.json").is_file():
        return candidate / "results.json"
    data_dir = data_dir or os.environ.get("TAU2_DATA_DIR")
    if not data_dir:
        raise FileNotFoundError(f"{run} is not a results file and TAU2_DATA_DIR is not set")
    path = Path(data_dir) / "simulations" / run / "results.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def read_results(path: Path, source_tz: ZoneInfo) -> SavedRun:
    """Parse ``results.json`` at ``path``; ``source_tz`` is the zone its times were written in."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    info = data.get("info") or {}
    agent = info.get("agent_info") or {}
    user = info.get("user_info") or {}
    environment = info.get("environment_info") or {}
    return SavedRun(
        run_name=Path(path).parent.name,
        agent_implementation=agent.get("implementation"),
        model_id=agent.get("llm"),
        user_model_id=user.get("llm"),
        policy_text=environment.get("policy"),
        simulations=tuple(_simulation(s, source_tz) for s in data.get("simulations") or []),
    )


def _simulation(raw: Mapping[str, Any], source_tz: ZoneInfo) -> SavedSimulation:
    return SavedSimulation(
        simulation_id=raw["id"],
        task_id=str(raw["task_id"]),
        start_time=to_utc(raw["start_time"], source_tz),
        duration_seconds=float(raw["duration"]),
        termination_reason=raw.get("termination_reason"),
        messages=tuple(_message(m, source_tz) for m in raw.get("messages") or []),
        reward=_reward(raw.get("reward_info")),
    )


def _message(raw: Mapping[str, Any], source_tz: ZoneInfo) -> Message:
    usage = raw.get("usage")
    return Message(
        role=raw["role"],
        turn_index=raw["turn_idx"],
        timestamp=to_utc(raw["timestamp"], source_tz),
        content=raw.get("content"),
        tool_calls=tuple(
            ToolCall(call_id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
            for c in raw.get("tool_calls") or []
        ),
        tool_call_id=raw.get("id") if raw["role"] == "tool" else None,
        error=bool(raw.get("error")),
        execution_status=raw.get("execution_status"),
        tokens=TokenUsage(usage["prompt_tokens"], usage["completion_tokens"]) if usage else None,
        cost=raw.get("cost"),
        generation_seconds=raw.get("generation_time_seconds"),
    )


def _reward(raw: Mapping[str, Any] | None) -> Reward | None:
    if not raw or raw.get("reward") is None:
        return None
    return Reward(
        reward=float(raw["reward"]),
        breakdown=raw.get("reward_breakdown") or {},
        basis=tuple(raw.get("reward_basis") or ()),
        details={key: raw[key] for key in _REWARD_DETAILS if raw.get(key) is not None},
    )
