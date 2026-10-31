"""Measure hooks C and E on a cassette replay: offline, and nothing is sent.

Run with:

    uv run python -m agent_autonomy_lab.entrypoints.hook_probe c \\
        --cassette cassettes/m3-07-task-0.cassette.json \\
        --reference ../tau2-bench/data/simulations/m3-07-replay-off/outcome.json

ADR-016 compares the LangSmith hook points (docs/BASELINE.md), and no other
Milestone 3 run uses these two. Each mode replays the cassette through
upstream's agent, unwrapped, and reports replay times and an outcome digest:

- ``plain``: nothing added, the times to compare with.
- ``c``: a LiteLLM callback that counts model calls, tokens, and cost. It is
  not LiteLLM's LangSmith integration. It then runs τ-bench's NL judge once
  against a canned response, because the cassette's task calls no judge.
- ``e``: a pass-through wrapper on the run's live ``get_response`` that times
  each tool call.

Run one mode per process. LiteLLM drops a second ``CustomLogger`` of the same
class without saying so, and this module keeps one for that reason. The first
replay of each mode is a warm-up and is not timed.
"""

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import litellm
from litellm.integrations.custom_logger import CustomLogger
from tau2.config import DEFAULT_LLM_NL_ASSERTIONS
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.environment.environment import Environment
from tau2.evaluator.evaluator_nl_assertions import NLAssertionsEvaluator
from tau2.runner import build_text_orchestrator, get_tasks, run_simulation

from agent_autonomy_lab.adapters.litellm import Cassette, litellm_transport, replaying
from agent_autonomy_lab.adapters.tau2.live_run import (
    LiveRunSettings,
    register_untraced_agent,
    run_untraced_task,
    trial_seed,
)
from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.adapters.tau2.traced_agent import UntracedAgentFactory
from agent_autonomy_lab.domain.equivalence import RunOutcome
from agent_autonomy_lab.domain.events import canonical_json

MODES = ("plain", "c", "e")
CALLBACK_WAIT_SECONDS = 5.0
CANNED_JUDGE_TOKENS = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


class CallCounter(CustomLogger):
    """Hook C: what a LiteLLM success callback receives for each model call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.own_ms = 0.0

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        began = time.perf_counter()
        usage = getattr(response_obj, "usage", None)
        self.calls.append(
            {
                "model": kwargs.get("model"),
                "cost": kwargs.get("response_cost"),
                "prompt": getattr(usage, "prompt_tokens", 0),
                "completion": getattr(usage, "completion_tokens", 0),
            }
        )
        self.own_ms += (time.perf_counter() - began) * 1000

    def wait_for(self, count: int) -> None:
        """LiteLLM calls back on a background thread; give it time to reach ``count``."""
        deadline = time.monotonic() + CALLBACK_WAIT_SECONDS
        while len(self.calls) < count and time.monotonic() < deadline:
            time.sleep(0.01)


COUNTER = CallCounter()


@dataclass
class ToolTimer:
    """Hook E: what a wrapper on ``get_response`` sees for each tool call."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    own_ms: float = 0.0

    def install(self, environment: Environment) -> None:
        """Wrap this ``environment`` only; evaluation builds its own."""
        passthrough = environment.get_response

        def get_response(message: ToolCall) -> ToolMessage:
            began = time.perf_counter()
            result = passthrough(message)
            returned = time.perf_counter()
            self.calls.append(
                {
                    "name": message.name,
                    "error": result.error,
                    "tool_ms": (returned - began) * 1000,
                }
            )
            self.own_ms += (time.perf_counter() - returned) * 1000
            return result

        environment.get_response = get_response


def _digest(outcome: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(outcome).encode("utf-8")).hexdigest()


def _wrapped_outcome(
    settings: LiveRunSettings, factory: UntracedAgentFactory, timer: ToolTimer
) -> RunOutcome:
    """Run the task through ``timer``'s wrapper, as a fault run reaches its environment."""
    config = settings.config(factory.agent_version)
    (task,) = get_tasks(settings.domain, task_ids=[settings.task_id])
    orchestrator = build_text_orchestrator(config, task, seed=trial_seed(settings.seed))
    timer.install(orchestrator.environment)
    result = run_simulation(orchestrator)
    local_tz = datetime.now().astimezone().tzinfo
    simulation = simulation_from_dict(result.model_dump(mode="json"), local_tz)
    return RunOutcome.of(simulation, factory.toolkits[-1].get_db_hash())


def _replay(cassette: Cassette, settings: LiveRunSettings, mode: str) -> dict[str, Any]:
    factory = register_untraced_agent(f"hook_probe_{uuid4().hex[:8]}")
    timer = ToolTimer()
    with replaying(cassette) as transport:
        began = time.perf_counter()
        if mode == "e":
            outcome = _wrapped_outcome(settings, factory, timer)
        else:
            run = run_untraced_task(settings, factory)
            outcome = RunOutcome.of(run.simulation, run.final_db_hash)
        elapsed_ms = (time.perf_counter() - began) * 1000
        transport.check_complete()
    return {
        "elapsed_ms": elapsed_ms,
        "served": transport.served,
        "digest": _digest(outcome.to_dict()),
        "timer": timer,
    }


def _canned_judge_reply(request: httpx.Request) -> httpx.Response:
    answer = {"results": [{"expectedOutcome": "x", "metExpectation": True, "reasoning": "r"}]}
    return httpx.Response(
        200,
        json={
            "id": "resp_probe",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": DEFAULT_LLM_NL_ASSERTIONS,
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "type": "message",
                    "id": "msg_probe",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": json.dumps(answer), "annotations": []}
                    ],
                }
            ],
            "usage": CANNED_JUDGE_TOKENS,
        },
    )


def _judge_call() -> dict[str, Any]:
    """Whether the callback sees τ-bench's NL judge; the usage is the canned reply's."""
    before = len(COUNTER.calls)
    # The empty cassette puts a placeholder in place of any real provider key.
    with replaying(Cassette(interactions=())):
        with litellm_transport(httpx.MockTransport(_canned_judge_reply)):
            NLAssertionsEvaluator.evaluate_nl_assertions(
                [
                    UserMessage(role="user", content="hi"),
                    AssistantMessage(role="assistant", content="hello"),
                ],
                ["x"],
            )
    COUNTER.wait_for(before + 1)
    return {"judge_model": DEFAULT_LLM_NL_ASSERTIONS, "callback_saw": COUNTER.calls[before:]}


def _callback_report(calls_per_replay: list[int]) -> dict[str, Any]:
    last = COUNTER.calls[-calls_per_replay[-1] :] if calls_per_replay[-1] else []
    return {
        "calls_per_replay": sorted(set(calls_per_replay)),
        "models": sorted({str(call["model"]) for call in last}),
        "prompt_tokens": sum(call["prompt"] for call in last),
        "completion_tokens": sum(call["completion"] for call in last),
        "cost_usd": sum(call["cost"] or 0 for call in last),
        "calls_without_cost": sum(call["cost"] is None for call in last),
        "callback_own_ms_all_replays": round(COUNTER.own_ms, 3),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--cassette", type=Path, required=True, help="cassette file")
    parser.add_argument("--repeats", type=int, default=9, help="timed replays after the warm-up")
    parser.add_argument(
        "--reference", type=Path, help="an outcome.json whose outcome each replay should equal"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.cassette.is_file():
        print(f"{args.cassette} is not a cassette file", file=sys.stderr)
        return 1
    cassette = Cassette.load(args.cassette)
    settings = LiveRunSettings(**cassette.settings)
    if args.mode == "c":
        litellm.callbacks = [COUNTER]

    replays, calls_per_replay = [], []
    for _ in range(args.repeats + 1):
        before = len(COUNTER.calls)
        replays.append(_replay(cassette, settings, args.mode))
        if args.mode == "c":
            COUNTER.wait_for(before + replays[-1]["served"])
        calls_per_replay.append(len(COUNTER.calls) - before)

    times = [replay["elapsed_ms"] for replay in replays[1:]]
    digests = sorted({replay["digest"] for replay in replays})
    report: dict[str, Any] = {
        "mode": args.mode,
        "timed_replays": len(times),
        "elapsed_ms": {
            "median": round(statistics.median(times), 1),
            "min": round(min(times), 1),
            "max": round(max(times), 1),
        },
        "served": sorted({replay["served"] for replay in replays}),
        "outcome_sha256": digests,
    }
    if args.reference:
        data = json.loads(args.reference.read_text(encoding="utf-8"))
        report["equal_to_reference"] = digests == [_digest(data["outcome"])]
    if args.mode == "c":
        report["callback"] = _callback_report(calls_per_replay)
        report["judge"] = _judge_call()
    if args.mode == "e":
        timer = replays[-1]["timer"]
        report["tool_calls"] = [
            {**call, "tool_ms": round(call["tool_ms"], 3)} for call in timer.calls
        ]
        report["wrapper_own_ms_per_replay"] = round(timer.own_ms, 4)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
