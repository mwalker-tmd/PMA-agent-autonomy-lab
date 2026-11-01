# Agent Autonomy Lab

**Status:** Proposal and specification; the lab Python project, its trace event schema, and a LangSmith trace adapter exist; the Milestone 2 baseline has been exported to LangSmith, one task has been run live through a traced lab agent, an injected tool failure has been inspected in its trace, and a thread-level evaluator has judged a traced conversation. Milestones 0 (repository and workflow setup), 1 (project and success criteria), 2 (install and run τ-bench), and 3 (integrate LangSmith observability) are complete; Milestone 4 (build the evaluation framework) is in progress.  
**Documentation baseline:** October 18, 2026.  
**Approach:** One repository for our project; the upstream [τ-bench repository](https://github.com/sierra-research/tau2-bench) remains separate.

**An AI support agent earns bounded autonomy through demonstrated performance.**

> Grant AI agents the autonomy they have demonstrated they can exercise safely and reliably. Expand that autonomy through evidence, and retain the ability to restrict it when performance changes.

## Why this project exists

Many AI demonstrations show that an agent can answer questions or use tools. This project demonstrates how a product organization can decide **whether it should be permitted to execute those tools**. We simulate multi-turn retail support interactions, inspect agent behavior, measure customer outcomes and policy compliance, and progressively authorize selected actions based on evidence.

It extends an earlier RAGAS/RAG-pipeline evaluation demonstration into **agent operations**: monitoring, observability, evaluation, incident response, regression testing, and governance.

## Documentation

| Document | Purpose |
| --- | --- |
| **[DEVELOPMENT.md](DEVELOPMENT.md)** | **Canonical execution plan: the nine delivery milestones (1–9), tasks, deliverables, and acceptance criteria.** |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development workflow: issues, labels, branches, commits, pull requests, Copilot review, and definition of done. |
| [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | Motivation, stakeholders, success outcomes, scope, teaching principles. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, end-to-end flow, boundaries, metadata, and interfaces. |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Metrics, rubrics, evaluation datasets, methods, and interpretation. |
| [docs/SIMULATION.md](docs/SIMULATION.md) | Synthetic traffic generation, scenario strategy, realism, and reproducibility. |
| [docs/AUTONOMY.md](docs/AUTONOMY.md) | Autonomy levels, runtime authorization gate, promotion and restriction governance. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Monitoring, alerts, version comparison, and regression-response playbook. |
| [docs/DEMO.md](docs/DEMO.md) | Office-hours narrative, live runbook, and teaching checkpoints. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Recorded architecture decisions, open questions, and changes. |

## Planned stack

- **Simulation:** τ-bench (the upstream `tau2-bench` repository), retail domain, text-mode multi-turn conversations.
- **Implementation:** Python 3.12 (τ-bench at the pinned commit does not import on 3.13); `uv`.
- **Telemetry/evaluation:** LangSmith (existing account); Python deterministic evaluators; sampled LLM and human judgments.
- **Authorization:** A custom, synchronous tool-boundary authorization gate independent of agent prompts.
- **Presentation:** Existing τ-bench viewer and LangSmith dashboards first; no custom UI unless required.

## Milestones

Milestone 0 set up the repository and workflow. Nine delivery milestones follow:

1. Establish project and success criteria.
2. Install and run τ-bench.
3. Integrate LangSmith observability.
4. Build the evaluation framework.
5. Generate simulated production traffic.
6. Implement earned autonomy.
7. Build operational monitoring.
8. Introduce and diagnose regressions.
9. Prepare the teaching demonstration.

See [DEVELOPMENT.md](DEVELOPMENT.md) for the steps and acceptance criteria for **every** milestone.

## Project state and next action

The lab's **tracing code, deterministic evaluators, and rubric judges exist; its confirmation judge, authorization gate, and traffic harness do not yet**. The first unmodified τ-bench baseline has run (see [docs/BASELINE.md](docs/BASELINE.md)). Milestone 0 set up the repository, issue templates, labels, and branch protection; see [CONTRIBUTING.md](CONTRIBUTING.md). Milestone 1 defined the scenarios, evaluation evidence, and success criteria, accepted in [ADR-012](docs/DECISIONS.md#adr-012--accept-the-milestone-1-scope-and-rubric). Milestone 2 ran an unmodified five-task τ-bench baseline, recorded what its score does and does not guarantee, mapped real retail tasks to the three workflows ([ADR-015](docs/DECISIONS.md#adr-015--map-real-retail-tasks-to-the-three-workflows)), and updated the Milestone 1 specifications to match. Milestone 3 added LangSmith tracing at the points chosen in [ADR-016](docs/DECISIONS.md#adr-016--instrument-live-runs-at-the-lab-agent-wrapper-and-saved-runs-by-export), with the principle that observability should let us understand the agent's behavior without changing it. A deterministic replay of one task [showed that tracing changes nothing](reports/m3-traces/behavioral-equivalence.md), and a [walkthrough](reports/m3-traces/walkthrough.md) explains an injected tool failure from its trace. The first working teaching checkpoint follows Milestone 4.

## Guardrails

All customer traffic is synthetic. Do not commit API keys or customer data. Never conflate fluent conversation with a verified business outcome. A blocked unauthorized action is a successful enforcement decision **and** an agent-behavior failure. Sample metrics and autonomy thresholds in this repository are proposals, **not measured results or production safety claims**.

## License

Released under the MIT License; see [LICENSE](LICENSE). Any upstream code incorporated later keeps its own copyright and license notices.

## References

- [τ-bench repository / setup](https://github.com/sierra-research/tau2-bench)
- [τ-bench CLI reference](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)
- [τ-bench agent examples](https://github.com/sierra-research/tau2-bench/blob/main/examples/agents/README.md)
- [LangSmith multi-turn online evaluation](https://docs.langchain.com/langsmith/online-evaluations-multi-turn)
- [LangSmith dashboards](https://docs.langchain.com/langsmith/dashboards)
