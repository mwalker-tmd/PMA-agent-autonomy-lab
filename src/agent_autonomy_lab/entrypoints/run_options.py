"""Options and output files shared by the live and replayed run entrypoints."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_autonomy_lab.adapters.litellm import Cassette
from agent_autonomy_lab.adapters.tau2.live_run import LiveRunSettings
from agent_autonomy_lab.domain.equivalence import RunOutcome

BASELINE_MODEL = "openai/responses/gpt-6-luna"
BASELINE_ARGS = '{"reasoning_effort": "medium"}'


def _json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"not JSON: {error}") from error
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def add_run_settings(parser: argparse.ArgumentParser) -> None:
    """The τ-bench run settings; every default matches the Milestone 2 baseline."""
    parser.add_argument("--task-id", default="0", help="retail task ID (default 0)")
    parser.add_argument("--agent-llm", default=BASELINE_MODEL)
    parser.add_argument("--user-llm", default=BASELINE_MODEL)
    parser.add_argument("--agent-llm-args", type=_json_object, default=BASELINE_ARGS)
    parser.add_argument("--user-llm-args", type=_json_object, default=BASELINE_ARGS)
    parser.add_argument("--seed", type=int, default=42, help="run seed, as for tau2 run")


def run_settings(args: argparse.Namespace) -> LiveRunSettings:
    return LiveRunSettings(
        task_id=args.task_id,
        agent_llm=args.agent_llm,
        user_llm=args.user_llm,
        agent_llm_args=args.agent_llm_args,
        user_llm_args=args.user_llm_args,
        seed=args.seed,
    )


def settings_data(settings: LiveRunSettings) -> dict[str, Any]:
    """``settings`` as the JSON a cassette stores."""
    return json.loads(json.dumps(asdict(settings)))


def settings_mismatch(settings: LiveRunSettings, cassette: Cassette) -> str | None:
    """Why ``settings`` cannot replay ``cassette``, or ``None`` if they can."""
    wanted = settings_data(settings)
    recorded = dict(cassette.settings)
    if wanted == recorded:
        return None
    names = sorted(k for k in wanted.keys() | recorded.keys() if wanted.get(k) != recorded.get(k))
    return f"the run's settings differ from the cassette's in: {', '.join(names)}"


def write_outcome(out_dir: Path, outcome: RunOutcome, provenance: dict[str, Any]) -> None:
    """Write ``outcome.json``, which ``compare_outcomes`` reads."""
    data = {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        **provenance,
        "outcome": outcome.to_dict(),
    }
    (out_dir / "outcome.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
