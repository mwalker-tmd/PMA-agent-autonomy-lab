"""Run one τ-bench task live through the traced lab agent.

Uses only τ-bench's public registry and runner, as its custom-agent examples
do: the traced agent is registered under its own name, the task is loaded
with ``get_tasks``, and ``run_single_task`` runs and evaluates it. The run
config is built from explicit settings because τ-bench's defaults
(``temperature: 0.0``, seed 300) are not the Milestone 2 baseline's.

``tau2 run`` derives each trial's seed from the run seed
(``random.seed(seed)`` then one ``randint(0, 1000000)`` per trial), so
``trial_seed`` repeats that for trial 0 and the live run gets the baseline's
per-simulation seed.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tau2.data_model.simulation import TextRunConfig
from tau2.registry import registry
from tau2.runner import build_text_orchestrator, get_tasks, run_single_task

from agent_autonomy_lab.adapters.tau2.results_reader import simulation_from_dict
from agent_autonomy_lab.adapters.tau2.traced_agent import TracedAgentFactory, fingerprint
from agent_autonomy_lab.domain.agent_turns import AgentFingerprint, LiveRecording
from agent_autonomy_lab.domain.events import policy_version
from agent_autonomy_lab.domain.simulations import SavedSimulation

AGENT_NAME = "lab_traced_llm_agent"
BASELINE_AGENT = "llm_agent"


@dataclass(frozen=True)
class LiveRunSettings:
    """The run's τ-bench settings; see reports/m2-baseline/manifest.yaml for the baseline's."""

    task_id: str
    agent_llm: str
    user_llm: str
    agent_llm_args: Mapping[str, Any] = field(default_factory=dict)
    user_llm_args: Mapping[str, Any] = field(default_factory=dict)
    seed: int = 42
    domain: str = "retail"
    max_steps: int = 200
    max_errors: int = 10

    def config(self, agent: str) -> TextRunConfig:
        return TextRunConfig(
            domain=self.domain,
            agent=agent,
            llm_agent=self.agent_llm,
            llm_args_agent=dict(self.agent_llm_args),
            user="user_simulator",
            llm_user=self.user_llm,
            llm_args_user=dict(self.user_llm_args),
            task_ids=[self.task_id],
            num_trials=1,
            max_concurrency=1,
            seed=self.seed,
            max_steps=self.max_steps,
            max_errors=self.max_errors,
        )


@dataclass(frozen=True)
class LiveRun:
    """A finished live run: τ-bench's result, the agent's recording, and the reference."""

    simulation: SavedSimulation
    recording: LiveRecording
    reference: AgentFingerprint
    trial_seed: int
    policy_version: str
    agent_name: str
    simulation_json: str
    language_context: Mapping[str, Any] = field(default_factory=dict)


def trial_seed(seed: int, trial: int = 0) -> int:
    """The seed ``tau2 run`` gives trial ``trial`` of a run seeded with ``seed``."""
    rng = random.Random(seed)
    return [rng.randint(0, 1_000_000) for _ in range(trial + 1)][trial]


def register_traced_agent(agent_name: str = AGENT_NAME) -> TracedAgentFactory:
    """Register a ``TracedAgentFactory`` under ``agent_name`` and return it.

    The registry is process-wide and refuses a name twice, so call this once
    per process.
    """
    factory = TracedAgentFactory(agent_name)
    registry.register_agent_factory(factory, agent_name)
    return factory


def reference(settings: LiveRunSettings, task: Any, seed: int) -> tuple[AgentFingerprint, str]:
    """What upstream's own ``llm_agent`` would send for this task and seed, and the policy version.

    Builds the baseline orchestrator with τ-bench's builder, sets the agent's
    seed as the orchestrator does when a run starts, and hashes its initial
    state. Nothing is run and no model is called. The policy version is the
    environment's retail policy, as in the saved-run export; the agent's copy
    adds a language clause, which ``prompt_version`` covers.
    """
    orchestrator = build_text_orchestrator(settings.config(BASELINE_AGENT), task, seed=seed)
    agent = orchestrator.agent
    agent.set_seed(seed)
    sent = fingerprint(agent, agent.get_init_state().system_messages)
    return sent, policy_version(orchestrator.environment.get_policy())


def run_traced_task(settings: LiveRunSettings, factory: TracedAgentFactory) -> LiveRun:
    """Run ``settings.task_id`` once through the agent ``factory`` was registered as."""
    config = settings.config(factory.agent_version)
    (task,) = get_tasks(settings.domain, task_ids=[settings.task_id])
    seed = trial_seed(settings.seed)
    expected, policy = reference(settings, task, seed)
    built = len(factory.recorders)
    result = run_single_task(config, task, seed=seed)
    if len(factory.recorders) != built + 1:
        raise RuntimeError(f"expected one traced agent, got {len(factory.recorders) - built}")
    local_tz = datetime.now().astimezone().tzinfo
    data = result.model_dump(mode="json")
    return LiveRun(
        simulation=simulation_from_dict(data, local_tz),
        recording=factory.recorders[-1].recording(),
        reference=expected,
        trial_seed=seed,
        policy_version=policy,
        agent_name=factory.agent_version,
        simulation_json=result.model_dump_json(indent=2),
        language_context=data.get("language_context") or {},
    )
