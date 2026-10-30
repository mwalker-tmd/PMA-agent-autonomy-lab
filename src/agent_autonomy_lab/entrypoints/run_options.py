"""Options and output files shared by the live and replayed run entrypoints."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_autonomy_lab.adapters.litellm import COVERED_PROVIDERS, Cassette, provider_of
from agent_autonomy_lab.adapters.tau2.live_run import LiveRunSettings
from agent_autonomy_lab.domain.equivalence import RunOutcome
from agent_autonomy_lab.domain.events import canonical_json

BASELINE_MODEL = "openai/responses/gpt-6-luna"
BASELINE_ARGS = '{"reasoning_effort": "medium"}'

# The model arguments a run accepts: tuning parameters that carry no
# credential. Model arguments are stored in a cassette's settings and a
# trace's model_args, and LiteLLM accepts credentials through many of its
# arguments (api_key, extra_headers, headers, api_base, ...), so this is an
# allowlist, not a list of known credential names. Keys come from the
# environment. Add a parameter here in a reviewed change when an experiment
# needs it.
MODEL_ARGS = frozenset(
    {
        "reasoning_effort",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "seed",
        "parallel_tool_calls",
        "stop",
        "drop_params",
        "num_retries",
    }
)


def _model_args(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(f"not JSON: {error}") from error
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    if refused := sorted(value.keys() - MODEL_ARGS):
        raise argparse.ArgumentTypeError(
            f"not an accepted model argument: {', '.join(refused)} "
            f"(accepted: {', '.join(sorted(MODEL_ARGS))}; credentials come from the environment)"
        )
    return value


def add_run_settings(parser: argparse.ArgumentParser) -> None:
    """The τ-bench run settings; every default matches the Milestone 2 baseline."""
    parser.add_argument("--task-id", default="0", help="retail task ID (default 0)")
    parser.add_argument("--agent-llm", default=BASELINE_MODEL)
    parser.add_argument("--user-llm", default=BASELINE_MODEL)
    parser.add_argument("--agent-llm-args", type=_model_args, default=BASELINE_ARGS)
    parser.add_argument("--user-llm-args", type=_model_args, default=BASELINE_ARGS)
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


def uncovered_models(settings: LiveRunSettings) -> str | None:
    """Why ``settings`` cannot be recorded or replayed, or ``None`` if they can.

    A cassette run accepts only models whose provider route the cassette
    transports are tested on (``COVERED_PROVIDERS``); any other provider's
    client might bypass them, so a recording would miss its calls and a
    replay could reach the provider.
    """
    models = {"agent_llm": settings.agent_llm, "user_llm": settings.user_llm}
    refused = {name: m for name, m in models.items() if provider_of(m) not in COVERED_PROVIDERS}
    if not refused:
        return None
    listed = ", ".join(
        f"{name} {model!r} ({provider_of(model)})" for name, model in refused.items()
    )
    return f"a cassette run needs models from {', '.join(COVERED_PROVIDERS)}; refused: {listed}"


def settings_mismatch(settings: LiveRunSettings, cassette: Cassette) -> str | None:
    """Why ``settings`` cannot replay ``cassette``, or ``None`` if they can."""
    wanted = settings_data(settings)
    recorded = dict(cassette.settings)
    names = sorted(
        k
        for k in wanted.keys() | recorded.keys()
        if k not in wanted
        or k not in recorded
        or canonical_json(wanted[k]) != canonical_json(recorded[k])
    )
    if not names:
        return None
    return f"the run's settings differ from the cassette's in: {', '.join(names)}"


def write_outcome(out_dir: Path, outcome: RunOutcome, provenance: dict[str, Any]) -> None:
    """Write ``outcome.json``, which ``compare_outcomes`` reads."""
    data = {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        **provenance,
        "outcome": outcome.to_dict(),
    }
    (out_dir / "outcome.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
