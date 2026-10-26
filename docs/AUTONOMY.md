# Earned Autonomy — Authorization and Governance Specification

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD) · [Evaluation](EVALUATION.md) · [Architecture](ARCHITECTURE.md)

**Status:** Proposed levels and policies. No gate or promotion logic has been implemented. Thresholds below are **illustrative teaching values, not approved production controls**.

## Central principle

An agent receives **the narrowest authority supported by evidence**, reviewed by an accountable person and enforced by software. Higher autonomy is not a reward for producing fluent answers; it is a decision about **permitted action scope** under explicit safeguards.

This mirrors the practical progression of a human support employee: observe/read first, act under supervision next, gain autonomy on routine cases only after reliably meeting requirements, and return to supervision if performance or risk changes.

## What “autonomy” means here

Autonomy is **permission to execute specific operations without additional employee sign-off**, subject to all existing customer and business controls. It never means permission to skip customer identity checks, customer confirmation, statutory rules, order eligibility, logging or audit.

**Important separation**

- **Customer authorization / consent:** The customer is authenticated/entitled and has explicitly approved the consequential operation where policy requires it.
- **Human operational approval:** A support representative or designated reviewer authorizes the agent to execute a transaction when its autonomy level requires oversight.

A Level 2 agent might not need **operational** approval for eligible cancellations. It still needs customer authorization and required confirmation.

## Proposed four levels

| Level | Label | Agent may do | Still requires |
| --- | --- | --- | --- |
| 0 | Trainee (read-only) | Answer questions, look up authorized order data | Customer authentication for protected data; no writes |
| 1 | Supervised | Prepare eligible state-changing requests, propose tool arguments | Customer authorization, policy checks, **human employee approval** before write |
| 2 | Trusted within bounds | Execute explicitly granted **eligible customer-confirmed pending-order cancellations** | Customer authorization, policy/argument checks, transaction logging; human review for other actions |
| 3 | Advanced within bounds | Execute additional specifically approved return/exchange categories | All mandatory customer/business controls; human approval for excluded/high-risk operations |

A level alone does not grant a blanket action allowlist. Pair it with **explicit per-tool/action scope**. “Level 3” is not unlimited autonomy.

## Proposed policy matrix (must align to actual τ-bench tools)

| Action category | Level 0 | Level 1 | Level 2 | Level 3 |
| --- | --- | --- | --- | --- |
| Order status lookup | Allowed with required identity/authorization | Allowed | Allowed | Allowed |
| Eligible pending-order cancellation | Deny execution | Human operational approval | Allowed within explicit bounds | Allowed within explicit bounds |
| Eligible return/exchange | Deny execution | Human operational approval | Human operational approval by default | Allowed only for approved subcategories |
| Ineligible/wrong-account/high-risk action | Deny | Deny or escalate | Deny or escalate | Deny or escalate |
| Unknown/new tool or malformed request | **Deny** | **Deny** | **Deny** | **Deny** |

**Caveat:** Exact action and eligibility mapping will be based on actual retail tools/policies discovered in Milestone 2; these rows are the teaching policy abstraction.

## Synchronous enforcement design

The authorization gate must run **before the real simulated mutating tool** and be impossible for model instructions to bypass.

```text
Agent proposes action
    |
    v
Validate tool/action in allowlist ── No ──> DENY + record
    |
   Yes
    v
Validate arguments, customer entitlement, business eligibility ── Fail ──> DENY
    |
   Pass
    v
Check required customer confirmation ── Missing ──> DENY / request confirmation
    |
   Present
    v
Check active agent level + scoped grants ── Not permitted ──> DENY + record
    |                          |
   Allowed                  Human review required (PENDING_APPROVAL)
    |                          |
    |                      Employee decision ── Reject ──> DENY + record
    |                          |
    |                       Approve
    v                          v
Execute once, with idempotency/audit where possible
    |
    v
Record execution result or error; return verified state to agent
```

### Required invariants

- No mutating business tool is directly exposed via another unguarded path.
- Default deny for missing/invalid context, unknown tool, malformed arguments or unknown policy version.
- Identity, order authorization and confirmation must match the **exact action target**, not merely exist somewhere earlier in the conversation.
- Every approval is tied to a specific action and arguments; subsequent argument change invalidates prior approval.
- Rechecks occur at **execution time**, not just when the model proposes the action.
- The tool's actual result determines success/failure; the agent's claim is not evidence of execution.
- If a tool might be retried, prevent duplicate mutation where the simulated environment supports idempotency or explicit state checks.

### Distinguish events and metrics

| Event | Meaning | Agent quality | Gate quality |
| --- | --- | --- | --- |
| `ATTEMPTED` | Agent requested a protected action | Evaluate request against policy | N/A yet |
| `DENIED` | Gate rejected it before mutation | Could indicate failed behavior | Appropriate if disallowed |
| `PENDING_APPROVAL` | Awaits employee sign-off | Correct when required | Correct if properly held |
| `APPROVED` | All current prerequisites met | No outcome yet | Required before execution |
| `EXECUTED` | Mutating tool actually ran | Outcome determined by state | Must have valid approval |
| `EXECUTION_FAILED` | Authorized tool attempted but failed | May need proper recovery/escalation | Audit/handling required |

**Blocked unauthorized attempt = agent behavior failure, guardrail success.** This is a key teaching moment.

## Earned-authority evidence and provisional thresholds

| Criterion | For promotion from L1 → L2 | For promotion from L2 → L3 |
| --- | --- | --- |
| Task success | Illustrative ≥90% | Illustrative ≥95% |
| Policy compliance | Illustrative ≥98% | Illustrative ≥99% |
| Critical unauthorized executions | **0**, and investigate any attempted violation | **0**, and investigate any attempted violation |
| Disqualifying critical process violations (authentication, confirmation, critical unauthorized attempts), even when the gate blocked them | **0** in the evidence set, independent of aggregate compliance | **0** in the evidence set, independent of aggregate compliance |
| Escalation correctness | Illustrative ≥95% | Illustrative ≥98% |
| Representative scenarios and trial count | Required; size determined by risk | More rigorous evidence required |
| Human sign-off | Required | Required |

**These percentages are classroom placeholders**, not automatically valid business policy. For a real deployment, choose per-action severity thresholds, sample-size requirements, statistical confidence standards, and formal ownership. Zero observed critical failures in 100 conversations is not sufficient evidence for high-risk deployment.

### Promotion review packet

Before granting a new action scope, a designated reviewer receives:

1. Current and proposed action permissions.
2. Candidate agent version, prompt version, model and tool/gate policy version.
3. Relevant action-specific outcome scores and denominators; worst-case examples.
4. Critical policy violations and denied attempt counts, with trace links.
5. LLM-judge/human reviewer agreement notes where qualitative factors matter.
6. Held-out and regression test outcomes, uncertainty and known gaps.
7. Rollback plan, expiration/review date if applicable, and ownership.

**Promotion decision:** `APPROVE`, `REJECT`, or `REQUEST_MORE_EVIDENCE`, with human reviewer, reason, timestamp and applicable policy version.

### Restriction triggers and response

Critical or high-confidence events—such as unauthorized attempted mutation, authorization gate bypass, identity/consent failure, unexpected new mutating tool, or a quality trend across a controlled batch—may trigger *automatic restriction* of the affected capability. The restriction is a **configuration/enforcement change** at the gate, not just an alert or a prompt edit.

1. Restrict precisely the relevant action permission or autonomy scope.
2. Record trigger, active version, decision ID and time.
3. Prove the restricted tool cannot execute without required human sign-off.
4. Investigate traces, correct root cause and add regression coverage.
5. Review measured evidence before *human-authorized* restoration.

No blanket promotion should occur automatically on one score crossing a threshold.

## Governance state machine (planned)

```mermaid
stateDiagram-v2
    [*] --> Level0
    Level0 --> Level1: Evidence + reviewer approval
    Level1 --> Level2: Action-specific evidence + reviewer approval
    Level2 --> Level3: Higher-risk evidence + reviewer approval
    Level3 --> Restricted: Critical signal / manual restriction
    Level2 --> Restricted: Critical signal / manual restriction
    Level1 --> Restricted: Critical signal / manual restriction
    Restricted --> Level1: Fix + regression tests + reviewer approval
    Restricted --> Level2: Fix + sufficient evidence + reviewer approval
```

Restoration target level may be lower than the prior one. Recording a state name alone is not sufficient; actual per-action permissions in force are authoritative.

## Tests that must exist before the autonomy demo

- Deny mutation at L0 even with persuasive system/user prompt text.
- Require valid employee approval for a Level 1 cancellation.
- Allow Level 2 eligible cancellation **only** after matching customer confirmation and policy eligibility.
- Deny cancellation of an ineligible/wrong-customer order at every level.
- Deny altered arguments after an earlier approval for a different order.
- Deny unknown tool and unparseable request; record the attempted action safely.
- Verify no business state changes after a denied request.
- Restrict a previously allowed action and verify the next attempt is denied or held for review.
- Preserve trace correlation, reviewer identity (synthetic), decision code and policy version.

## What this does *not* claim

Completing the lab does **not** establish that an agent is legally compliant, safe for production, or qualified to handle real customers. It illustrates the engineering/governance pattern with controlled synthetic evidence.
