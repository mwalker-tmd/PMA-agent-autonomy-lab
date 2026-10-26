# Decision Record and Open Questions

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD)

**Status:** Initial planning decisions and proposed follow-ups as of October 18, 2026, plus the Milestone 1 acceptance (ADR-012, October 26, 2026); nothing here is implemented or tested. “Selected” means selected for the **plan**.

**Purpose:** Prevent silent architecture drift. Any material scope, stack, evaluation or autonomy change should be recorded here and reflected in the canonical [development plan](../DEVELOPMENT.MD).

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
| ADR-011 | 2026-10-18 | **Selected:** README short; `DEVELOPMENT.MD` authoritative; domain docs under `docs/` | Clear separation of purpose, execution and technical specifications | Documentation becomes unwieldy |
| ADR-012 | 2026-10-26 | **Accepted:** Milestone 1 teaching scope and initial rubric ([note](#adr-012--accept-the-milestone-1-scope-and-rubric)) | All Milestone 1 documents merged after review; remaining unknowns are tracked as open questions | Milestone 2 inspection contradicts the workflows or rubric |

## Decision notes

### ADR-012 — Accept the Milestone 1 scope and rubric
- Date: 2026-10-26
- Status: Accepted
- Context: Milestone 1 defines successful service, acceptable conduct, and trustworthy evaluation before any code is written. Its documents landed through pull requests #20–#23, #26, #27, and #29–#36, each reviewed by GitHub Copilot with every comment addressed before merge. The sign-off review (#19) checked every cross-document link and made terminology consistent across all documents.
- Options considered: accept the scope and rubric now and validate them against real τ-bench retail tasks in Milestone 2; defer acceptance until after that inspection; revise further before accepting.
- Decision: Accept the teaching scope in [PROJECT_BRIEF.md](PROJECT_BRIEF.md) and the initial rubric in [EVALUATION.md](EVALUATION.md) (scorecard, scenario acceptance contracts, ground-truth boundaries, and judge rubrics) as the Milestone 1 baseline. The sole maintainer accepts them, acting as stakeholder, product manager, engineer, and SDET, informed by the Copilot pull request reviews (see the single-maintainer exception in [CONTRIBUTING.md](../CONTRIBUTING.md#protection-of-main)).
- Why: Milestone 2 needs a fixed rubric to inspect real tasks against. The rubric's known unknowns are recorded as open questions targeting Milestones 2–8, so accepting now does not hide them.
- Tradeoffs/risks: Retail task IDs, tool names, and policies are unverified until Milestone 2; autonomy thresholds are illustrative; one maintainer plus automated review is weaker than independent human review.
- Artifacts affected: `DEVELOPMENT.MD` (Milestone 1 marked complete), `README.md` (status), this log.
- Verification or reconsideration trigger: Milestone 2 inspection shows the retail tasks or policies do not support the three workflows or the rubric (see the [scope review triggers](PROJECT_BRIEF.md#scope-review-triggers)), or a second maintainer joins.

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
| How will human approvals be simulated: deterministic fixture, manual UI, or both? | 6 | Choose simplest auditable and teachable option |
| What action-specific sample sizes/thresholds are acceptable for the *demo*? | 4–6 | Calibrate rubric from actual baseline; mark limits explicitly |
| Are LangSmith dashboards/alerts enough for the final operations story? | 7 | Prototype one operations and one policy dashboard |
| How much variation occurs across repeated synthetic runs? | 5, 8 | Repeat selected tasks; report spread and sample size |

## Change procedure

1. State the decision required and the evidence available.
2. Record options (including retaining the current choice), their tradeoffs and risks.
3. Select and assign an effective date; record any migration steps.
4. Update impacted specs and `DEVELOPMENT.MD`, including milestone tasks, deliverables and acceptance criteria.
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
