# Decision Record and Open Questions

[Home](../README.md) · [Master plan](../DEVELOPMENT.md)

**Status:** Initial planning decisions and proposed follow-ups as of October 18, 2026, plus the Milestone 1 acceptance (ADR-012, October 26, 2026) and the code architecture (ADR-013, October 26, 2026); nothing here is implemented or tested. “Selected” means selected for the **plan**.

**Purpose:** Prevent silent architecture drift. Any material scope, stack, evaluation or autonomy change should be recorded here and reflected in the canonical [development plan](../DEVELOPMENT.md).

## Decision log

| ID | Date | Decision / status | Rationale | Revisit when |
| --- | --- | --- | --- | --- |
| ADR-001 | 2026-10-18 | **Selected:** One `agent-autonomy-lab` repository for our own code/docs | Simpler teaching, versioning, and deployment | A reusable component has real independent consumers |
| ADR-002 | 2026-10-18 | **Selected:** Upstream τ-bench maintained as a separate, pinned checkout/dependency | Preserve baseline comparability; avoid vendoring | Documented extension points are insufficient |
| ADR-003 | 2026-10-18 | **Selected:** Retail / text-mode simulations first | Common domain, state-changing actions, multi-turn support | Actual task/policy set conflicts with teaching goals |
| ADR-004 | 2026-10-18 | **Selected:** LangSmith first | Existing account; supports tracing, evaluation and dashboards | Feature constraints, costs, or workflow limitations arise |
| ADR-005 | 2026-10-18 | **Selected:** Python 3.12+ and `uv` | Aligns with upstream requirements and developer environment | Upstream compatibility changes |
| ADR-006 | 2026-10-18 | **Selected:** Agent permissions enforced in a synchronous gate before tools | An LLM instruction or async judge cannot enforce authority | Never weaken this security invariant |
| ADR-007 | 2026-10-18 | **Selected:** Promotions human-reviewed and action-scoped; restrictions may be automatic | Accountability, fail-safe behavior and minimum authority | Evidence-based governance model evolves |
| ADR-008 | 2026-10-18 | **Selected:** Outcome, compliance, and customer experience scored separately | Avoid hiding serious violations behind fluent/successful responses | If more metrics require explicit split |
| ADR-009 | 2026-10-18 | **Selected:** τ-bench official baseline separate from customized experiments | Defensible, reproducible reporting | If a published benchmark protocol explicitly supports modifications |
| ADR-010 | 2026-10-18 | **Selected:** No custom frontend for the first release | Prioritize the teaching workflow rather than presentation infrastructure | LangSmith + viewer cannot show essential evidence |
| ADR-011 | 2026-10-18 | **Selected:** README short; `DEVELOPMENT.md` authoritative; domain docs under `docs/` | Clear separation of purpose, execution and technical specifications | Documentation becomes unwieldy |
| ADR-012 | 2026-10-26 | **Accepted:** Milestone 1 teaching scope and initial rubric ([note](#adr-012--accept-the-milestone-1-scope-and-rubric)) | All Milestone 1 documents merged after review; remaining unknowns are tracked as open questions | Milestone 2 inspection contradicts the workflows or rubric |
| ADR-013 | 2026-10-26 | **Accepted:** Ports-and-adapters (Clean Architecture) for all lab code ([note](#adr-013--adopt-the-ports-and-adapters-architecture)) | Keeps τ-bench, LangSmith, the model provider, and the approval mechanism swappable, and the gate testable without them | A port has one adapter and no test double after Milestone 6, or the layering costs more than it isolates |

## Decision notes

### ADR-012 — Accept the Milestone 1 scope and rubric
- Date: 2026-10-26
- Status: Accepted
- Context: Milestone 1 defines successful service, acceptable conduct, and trustworthy evaluation before any code is written. Its documents landed through pull requests #20–#23, #26, #27, and #29–#36, each reviewed by GitHub Copilot with every comment addressed before merge. The sign-off review (#19) checked every cross-document link and made terminology consistent across all documents.
- Options considered: accept the scope and rubric now and validate them against real τ-bench retail tasks in Milestone 2; defer acceptance until after that inspection; revise further before accepting.
- Decision: Accept the teaching scope in [PROJECT_BRIEF.md](PROJECT_BRIEF.md) and the initial rubric in [EVALUATION.md](EVALUATION.md) (scorecard, scenario acceptance contracts, ground-truth boundaries, and judge rubrics) as the Milestone 1 baseline. The sole maintainer accepts them, acting as stakeholder, product manager, engineer, and SDET, informed by the Copilot pull request reviews (see the single-maintainer exception in [CONTRIBUTING.md](../CONTRIBUTING.md#protection-of-main)).
- Why: Milestone 2 needs a fixed rubric to inspect real tasks against. The rubric's known unknowns are recorded as open questions targeting Milestones 2–8, so accepting now does not hide them.
- Tradeoffs/risks: Retail task IDs, tool names, and policies are unverified until Milestone 2; autonomy thresholds are illustrative; one maintainer plus automated review is weaker than independent human review.
- Artifacts affected: `DEVELOPMENT.md` (Milestone 1 marked complete), `README.md` (status), this log.
- Verification or reconsideration trigger: Milestone 2 inspection shows the retail tasks or policies do not support the three workflows or the rubric (see the [scope review triggers](PROJECT_BRIEF.md#scope-review-triggers)), or a second maintainer joins.

### ADR-013 — Adopt the ports-and-adapters architecture
- Date: 2026-10-26
- Status: Accepted
- Context: Implementation starts in Milestone 3. The lab depends on four things that may change or be replaced: the benchmark (τ-bench, pinned per [ADR-002](#decision-log)), the tracing platform (LangSmith, [ADR-004](#decision-log)), the model provider (OpenAI `gpt-6-luna` for the baseline), and the human-approval mechanism (undecided until Milestone 6). The authorization gate is the project's security invariant ([ADR-006](#decision-log)) and must be testable without any of them: a gate test that needs a live model, a LangSmith project, or a τ-bench run is slow, costly, and nondeterministic. Milestone 2 inspection ([BASELINE.md](BASELINE.md#tool-inventory)) confirmed that τ-bench decides which retail tools mutate state, so the lab needs a seam at the tool-execution boundary that it controls.
- Options considered:
  1. **Layered** (presentation → service → data access, as in typical web templates): familiar, but lower layers still import infrastructure directly, so the gate would depend on τ-bench and LangSmith types.
  2. **Ports-and-adapters**: the domain and application core define interfaces (ports); infrastructure-specific code implements them (adapters) and is wired together at one composition root.
  3. **Organize by feature only** (the earlier planned layout: `integration/`, `observability/`, `evaluation/`, `simulation/`, `autonomy/`, `monitoring/`): easy to navigate, but sets no dependency direction, so nothing stops `autonomy/` from importing τ-bench or LangSmith.
- Decision: Use ports-and-adapters for all code under `src/agent_autonomy_lab/`. The core ports are `ToolExecutor`, `AuthorizationGate`, `TraceSink`, `ApprovalSource`, `Evaluator`, and `ModelClient` (see [ARCHITECTURE.md](ARCHITECTURE.md#code-structure-ports-and-adapters)). **Dependency rule:** dependencies point inward; domain and application code never imports τ-bench, LangSmith, or a provider SDK. Only adapters and the composition root do. Feature grouping is kept inside each layer (for example `domain/autonomy/`), so option 3's navigability is not lost.
- Why: Each external dependency is replaceable behind one port, and every port has an in-memory test double, so gate, policy, and evaluator logic can be unit-tested deterministically. The baseline stays comparable ([ADR-009](#decision-log)) because τ-bench is only reached through adapters, never edited.
- Tradeoffs/risks: More modules and indirection than a small lab strictly needs; ports designed before real use may have the wrong shape, so each port is kept minimal and revised when its first adapter is built. A port alone does not enforce the gate: `ToolExecutor` must be the only route to τ-bench's mutating tools, which Milestone 6 must still prove (see the open question on the tool-execution boundary). The rule is enforced with `import-linter` contracts once code starts in Milestone 3; until then it is a review rule.
- Artifacts affected: [ARCHITECTURE.md](ARCHITECTURE.md#code-structure-ports-and-adapters) (ports, adapters, dependency rule), [`DEVELOPMENT.md`](../DEVELOPMENT.md#suggested-artifact-layout-as-implementation-grows) (planned `src/` layout, Milestone 3 enforcement step), [CONTRIBUTING.md](../CONTRIBUTING.md#architecture) (architecture rule and pull request checklist item), the pull request template, and the Milestone 6 open question on human approvals below.
- Verification or reconsideration trigger: From Milestone 3, `import-linter` passes on every pull request. Reconsider if, after Milestone 6, a port still has only one adapter and no test double, or if keeping adapters thin forces business logic out of the core.

## Open questions to resolve deliberately

| Question | Target milestone | Preferred way to decide |
| --- | --- | --- |
| Which actual retail task IDs support order lookup, pending cancellation, and return/exchange? | 2 | Inspect task dataset, policies, tool schema, and sample trajectories |
| Which agent extension point is the narrowest safe place for LangSmith instrumentation? | 2–3 | Prototype against upstream examples without modifying core benchmark behavior |
| Which exact tool execution boundary permits complete gate enforcement? | 2, 6 | Trace real call path; test blocked mutations and alternative routes |
| Which agent and simulated-user models balance affordability and reliability? | 2 | Run five tasks, measure usage and model compatibility |
| How should tool correctness be judged when a task's reference `actions` are one valid trajectory, not the only correct one? (Affects the Tool correctness method in the [evaluation scorecard](EVALUATION.md#initial-scorecard--evidence-matrix).) | 2, 4 | Inspect real retail tasks; compare runs that reach the correct end state by different paths; define allowed-alternative and prohibited-action rules rather than exact trajectory match |
| Which policy checks can be deterministic from actual data? | 2–4 | Review policy/event/state schema; implement unit-test fixtures |
| Can the current LangSmith account/region access required thread evaluator/trajectory views? | 3–4 | Validate with one test thread and judge |
| How will human approvals be simulated: deterministic fixture, manual UI, or both? | 6 | Choose simplest auditable and teachable option. Either way it is an adapter behind the `ApprovalSource` port ([ADR-013](#adr-013--adopt-the-ports-and-adapters-architecture)). If a manual UI is chosen, it becomes a FastAPI adapter; `PMA-full-stack-fastapi-template` is the candidate source for its setup, not the project base, because a frontend is out of scope ([PROJECT_BRIEF.md](PROJECT_BRIEF.md#scope-boundaries)) and the template's layered layout conflicts with ADR-013 |
| What action-specific sample sizes/thresholds are acceptable for the *demo*? | 4–6 | Calibrate rubric from actual baseline; mark limits explicitly |
| Are LangSmith dashboards/alerts enough for the final operations story? | 7 | Prototype one operations and one policy dashboard |
| How much variation occurs across repeated synthetic runs? | 5, 8 | Repeat selected tasks; report spread and sample size |

## Change procedure

1. State the decision required and the evidence available.
2. Record options (including retaining the current choice), their tradeoffs and risks.
3. Select and assign an effective date; record any migration steps.
4. Update impacted specs and `DEVELOPMENT.md`, including milestone tasks, deliverables and acceptance criteria.
5. If the decision affects scoring comparability, **start a new labeled experiment version**.

## Decision note template

```markdown
### ADR-NNN — <short title>
- Date:
- Status: Proposed | Accepted | Rejected | Superseded
- Context:
- Options considered:
- Decision:
- Why:
- Tradeoffs/risks:
- Artifacts affected:
- Verification or reconsideration trigger:
```

## Deferred ideas (not commitments)

- Port a reusable authorization gate to other agent frameworks after it has demonstrated value in this lab.
- Add OpenTelemetry portability or compare Opik/Langfuse after LangSmith-based MVP works.
- Expand to telecom, banking knowledge, or voice only if it clearly improves a later lesson.
- Build an application UI or deploy publicly only if existing trace/dashboard viewers fail to communicate the core concepts.
