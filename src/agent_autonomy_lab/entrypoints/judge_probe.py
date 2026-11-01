"""Confirm the judge model works through the pinned LiteLLM, and record the evidence.

Run with (live; costs far less than a cent):

    uv run --env-file .env --env-file ../tau2-bench/.env \\
        python -m agent_autonomy_lab.entrypoints.judge_probe \\
        --recording tests/fixtures/judge-probe/claude-haiku-5-5.json

It sends one synthetic request with the declared judge settings, recording it,
and then two requests with settings the judge does not declare, to show what the
provider does with them. The recording is written once and never overwritten,
and ``tests/test_model_client.py`` replays it. The command prints a JSON report
for ``reports/m4-judge-runtime/manifest.yaml`` and exits 1 unless the declared
call works and LiteLLM prices it at the list price (docs/DECISIONS.md, ADR-020).
"""

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from agent_autonomy_lab.adapters.litellm import RecordingTransport, litellm_transport
from agent_autonomy_lab.adapters.models import LiteLLMModelClient
from agent_autonomy_lab.domain.evaluation import JUDGE_MODEL, JUDGE_SETTINGS, judge_request
from agent_autonomy_lab.domain.model_calls import ModelCallError, ModelMessage, ModelRequest

SYSTEM = "You check a retail support transcript. Reply with JSON only."
USER = (
    'Agent: "Shall I cancel order #W0000000 because it was ordered by mistake?"\n'
    'Customer: "Yes, please."\n'
    'Did the customer say yes? Reply with {"score": 2} if so, and {"score": 0} if not.'
)
LIST_PRICE_USD_PER_TOKEN = {"input": 0.10 / 1_000_000, "output": 0.50 / 1_000_000}
"""``claude-haiku-5-5`` up to 100K prompt tokens, from Anthropic's published price list."""
UNDECLARED: Mapping[str, Mapping[str, Any]] = {
    "reasoning_effort": {"max_tokens": 2048, "reasoning_effort": "low"},
    "temperature": {"max_tokens": 2048, "temperature": 0},
}


def declared_call(recording: Path) -> dict[str, Any]:
    client = LiteLLMModelClient()
    recorder = RecordingTransport()
    with litellm_transport(recorder):
        response = client.complete(judge_request(SYSTEM, USER))
    sha256 = recorder.cassette({"judge_probe": True}).save(recording)
    list_price = (
        response.input_tokens * LIST_PRICE_USD_PER_TOKEN["input"]
        + response.output_tokens * LIST_PRICE_USD_PER_TOKEN["output"]
    )
    return {
        "declared_settings": dict(JUDGE_SETTINGS),
        "effective_settings": dict(response.settings_sent),
        "requested_model": JUDGE_MODEL,
        "reported_model": response.model,
        "reply": response.text,
        "finish_reason": response.finish_reason,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "litellm_cost_usd": response.cost_usd,
        "list_price_cost_usd": list_price,
        "recording_sha256": sha256,
    }


def undeclared_call(settings: Mapping[str, Any]) -> dict[str, Any]:
    request = ModelRequest(
        model=JUDGE_MODEL,
        system=SYSTEM,
        messages=(ModelMessage("user", USER),),
        settings=settings,
    )
    try:
        response = LiteLLMModelClient().complete(request)
    except ModelCallError as error:
        return {"settings": dict(settings), "result": "failed", "error": str(error)}
    return {
        "settings": dict(settings),
        "result": "accepted",
        "effective_settings": dict(response.settings_sent),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recording", type=Path, required=True)
    args = parser.parse_args(argv)

    declared = declared_call(args.recording)
    report = {
        "litellm_version": version("litellm"),
        "declared_call": declared,
        "undeclared_calls": {name: undeclared_call(s) for name, s in UNDECLARED.items()},
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    priced = declared["litellm_cost_usd"] is not None and math.isclose(
        declared["litellm_cost_usd"], declared["list_price_cost_usd"], rel_tol=1e-6
    )
    return 0 if declared["reply"] and priced else 1


if __name__ == "__main__":
    sys.exit(main())
