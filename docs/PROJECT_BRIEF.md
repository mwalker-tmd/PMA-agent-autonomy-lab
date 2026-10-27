# Project Brief — Purpose, Outcomes and Scope

[Home](../README.md) · [Master implementation plan](../DEVELOPMENT.md)

**Status:** Planning proposal; no application implementation has begun.  
**Baseline:** October 18, 2026.

## Executive summary

**Agent Autonomy Lab** is a hands-on teaching project for **observability, monitoring, evaluation, and governance of an AI support agent**. It uses realistic, synthetic multi-round customer interactions, traces every important decision and tool action, evaluates outcomes and policy adherence, and demonstrates increasing/decreasing agent authority based on verified evidence.

The guiding product-management question is:

> Which actions can this agent perform reliably, what evidence supports that assessment, and what controls protect customers when it fails?

The goal is **verified customer resolution with an appropriate level of autonomy**, not automation for its own sake.

## Origin and teaching context

An earlier PM Accelerator exercise evaluated a RAG pipeline using RAGAS. The missing next lesson is that agent quality involves **actions** as well as answers: tools, permissions, state changes, escalation, multi-turn dialogue, outcomes, cost, and failure recovery.

An existing AI Support Agent Bot case study treats independence as earned responsibility, analogous to a new human employee: demonstrate skill under supervision, then receive more narrowly scoped authority. The analogy is about **organizational governance**, not treating a model as a person or allowing it to self-authorize.

### Who the demo serves

- **Product managers:** Define customer outcomes, risk tolerance, approvals, customer trust, and product-quality measures.
- **Engineering leaders:** Understand instrumentation, enforcement boundaries, release changes, and regressions.
- **AI engineers / technical learners:** Implement simulations, event traces, deterministic checks, and model-based evaluators.
- **Stakeholders/reviewers:** See why aggregate task accuracy alone is insufficient to expand an agent's authority.

## Learning outcomes

A participant should leave able to:

1. Distinguish **monitoring** (what is changing), **observability** (why it changed), **evaluation** (whether behavior/outcomes are acceptable), and **governance** (what actions are authorized).
2. Read an AI agent trace from customer request to model response, tool call, gate decision, business state, and customer communication.
3. Explain why outcome success and policy compliance require **separate measurements**.
4. Describe the difference between offline task-oracle scoring and online-style evaluation using available trace evidence.
5. Propose an autonomy rubric with **action-specific permissions** and an independent guardrail.
6. Show how a failing trace becomes a regression test and why a release might require a temporary autonomy restriction.
7. Identify uncertainty from limited synthetic samples and explain the role of sampled human review.

## Final demonstration story

A retail AI support agent begins with read-only access. It first helps with order lookup; later, after passing appropriate reviews, it can propose and execute eligible cancellations. The organization verifies task outcomes, policy compliance, and customer communication using traces and scoring. A prompt/model change causes premature cancellation **attempts**. A synchronous authorization gate blocks the action, monitoring raises a signal, observability explains the failure, and tests prevent recurrence. Authority is restored only after sufficient evidence and a recorded human decision.

The intended audience sees all four questions answered:

| Discipline | Question answered | Evidence shown |
| --- | --- | --- |
| Monitoring | Is quality or reliability degrading? | Trend, threshold, signal/alert |
| Observability | What actually happened in a specific interaction? | Thread, model call, tool call, gate decision |
| Evaluation | Did it achieve the correct outcome and follow policy? | Oracle outcome, deterministic checks, human/LLM judgments |
| Governance | Which actions may it perform right now, and why? | Permission policy, approval record, enforcement event |

## Initial product workflows

Each workflow is mapped to unmodified τ-bench retail tasks at the pinned commit `0c69df4` in [ADR-015](DECISIONS.md#adr-015--map-real-retail-tasks-to-the-three-workflows). The tasks' tools, policy rules, and gating checks are listed in [BASELINE.md](BASELINE.md#selected-tasks). No retail task is a pure order-status lookup, so workflow 1 is adapted to an order detail lookup.

1. **Order detail lookup (low risk).** An authenticated user asks about their own order: its status, total, items, or tracking. Desired result: accurate permitted read (`get_order_details`) and clear response; no mutation. Tasks: `68`, with `67` as an alternate on the same user and order (one scenario, not two); `43` carries the status and tracking content but ends in a write. *Adapted from "order status lookup"; the lookup tasks ask for an order total, not a status.*
2. **Pending-order cancellation (moderate risk).** Authenticated user requests cancellation of an order whose status is `pending`. Desired result: status check, explicit confirmation of the order ID and reason (`no longer needed` or `ordered by mistake`), `cancel_pending_order`, and correct final state. At Level 1, a simulated employee also approves. Tasks: `113` (two orders) and `88`.
3. **Return/exchange (higher complexity/risk).** User requests a return or exchange of items in a `delivered` order. Desired result: correct items, same-product available variants for an exchange, a refund to the original payment method or an existing gift card for a return, explicit confirmation, and state changes (`exchange_delivered_order_items` or `return_delivered_order_items`) only within authorization. Tasks: `0` (exchange) and `13` (return).

## Scope boundaries

**In scope**

- τ-bench retail text-mode simulation and actual supported tasks.
- Local Python/`uv` execution; synthetic customer personas and multi-turn tasks.
- LangSmith traces, threads, evaluators, dashboards and human review.
- Offline ground-truth scoring plus independent process checks.
- A simulated tool-execution approval service, authorization gate, and authorization audit log.
- Versioned traffic batches, simulated incidents, and a live/backup office-hours demo.

**Out of scope for the first release**

- Real customer transactions, real identities, payment rails, or production deployment.
- Building a frontend, call center, telephony pipeline, or generic SaaS platform.
- Proving statistically reliable rare-event safety from dozens of synthetic examples.
- Training a model or optimizing benchmark leaderboards.
- Forking τ-bench without a demonstrated technical necessity.
- Recreating a second RAG-specific evaluation tutorial.

## Definition of project success

The project is successful when it can reproduce one complete evidence chain:

**Customer request → agent/tool attempts → enforced permissions → business outcome → trace → eval score → monitoring signal → human autonomy decision → regression test.**

Every milestone's Acceptance Criteria are maintained in [DEVELOPMENT.md](../DEVELOPMENT.md).

## Core principles

- Start with **customer value and observed outcomes**, not automation percentage.
- Make agent autonomy **granular, evidence-based, revocable, and explicitly authorized**.
- Treat customer authentication/consent as separate from a human support agent's operational approval.
- Prefer objective checks for state and rules; use LLM judges for nuanced conversation qualities, with human calibration.
- Track failed attempts and prevented harm, not just actions that succeeded.
- Label all example values and scenario evidence honestly; never fabricate production claims.
- Keep the system simple enough to teach and small enough to inspect end to end.

## Scope review triggers

Update this brief and the decision log if: the retail tools don't support planned workflows, needed LangSmith functionality is unavailable on the account/region, the authorization gate cannot safely intercept tool execution, or API budgets require a smaller baseline.
