# Agent Autonomy Lab

**Status:** Proposal and specification; nothing is implemented. No application code, τ-bench baseline, or LangSmith integration exists yet. Milestone 0 (repository and workflow setup) is complete; Milestone 1 is in progress.  
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
| **[DEVELOPMENT.MD](DEVELOPMENT.MD)** | **Canonical execution plan: all nine milestones, tasks, deliverables, and exit criteria.** |
| [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | Motivation, stakeholders, success outcomes, scope, teaching principles. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, end-to-end flow, boundaries, metadata, and interfaces. |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Metrics, rubrics, evaluation datasets, methods, and interpretation. |
| [docs/SIMULATION.md](docs/SIMULATION.md) | Synthetic traffic generation, scenario strategy, realism, and reproducibility. |
| [docs/AUTONOMY.md](docs/AUTONOMY.md) | Autonomy levels, runtime policy gate, promotion and restriction governance. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Monitoring, alerts, version comparison, and regression-response playbook. |
| [docs/DEMO.md](docs/DEMO.md) | Office-hours narrative, live runbook, and teaching checkpoints. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Recorded architecture decisions, open questions, and changes. |

## Planned stack

- **Simulation:** τ-bench / τ³-bench, retail domain, text-mode multi-turn conversations.
- **Implementation:** Python 3.12 or 3.13; `uv`.
- **Telemetry/evaluation:** LangSmith (existing account); Python deterministic evaluators; sampled LLM and human judgments.
- **Authorization:** A custom, synchronous tool-boundary policy gate independent of agent prompts.
- **Presentation:** Existing τ-bench viewer and LangSmith dashboards first; no custom UI unless required.

## Nine milestones

1. Establish project and success criteria.
2. Install and run τ-bench.
3. Integrate LangSmith observability.
4. Build the evaluation framework.
5. Generate simulated production traffic.
6. Implement earned autonomy.
7. Build operational monitoring.
8. Introduce and diagnose regressions.
9. Prepare the teaching demonstration.

See [DEVELOPMENT.MD](DEVELOPMENT.MD) for the steps and acceptance criteria for **every** milestone.

## Project state and next action

We have **not yet run τ-bench or created application code**. Milestone 0 set up the repository, issue templates, labels, and branch protection; see [CONTRIBUTING.md](CONTRIBUTING.md). The immediate objective is Milestone 1: ratify the scenarios, evaluation evidence, and success criteria. Milestone 2 will install τ-bench and run the first five unmodified retail simulations. The first working teaching checkpoint follows Milestone 4.

## Guardrails

All customer traffic is synthetic. Do not commit API keys or customer data. Never conflate fluent conversation with a verified business outcome. A blocked unauthorized action is a successful enforcement decision **and** an agent-behavior failure. Sample metrics and autonomy thresholds in this repository are proposals, **not measured results or production safety claims**.

## License

**MIT is the recommended license**, but the license file and copyright holder should be created by the repository owner. Upstream code retains its own copyright/license notices if any is incorporated.

## References

- [τ-bench repository / setup](https://github.com/sierra-research/tau2-bench)
- [τ-bench CLI reference](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)
- [τ-bench agent examples](https://github.com/sierra-research/tau2-bench/blob/main/examples/agents/README.md)
- [LangSmith multi-turn online evaluation](https://docs.langchain.com/langsmith/online-evaluations-multi-turn)
- [LangSmith dashboards](https://docs.langchain.com/langsmith/dashboards)
