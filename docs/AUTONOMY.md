# Earned Autonomy — Authorization and Governance Specification

[Home](../README.md) · [Master plan](../DEVELOPMENT.md) · [Evaluation](EVALUATION.md) · [Architecture](ARCHITECTURE.md)

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

## Proposed policy matrix (retail tools)

Action categories map to the 16 retail agent tools at the pinned τ-bench commit `0c69df4` ([tool inventory](BASELINE.md#tool-inventory)): 7 `READ`, 2 `GENERIC`, and 7 `WRITE`. Only the `WRITE` tools change the database.

| Action category | Retail tools | Level 0 | Level 1 | Level 2 | Level 3 |
| --- | --- | --- | --- | --- | --- |
| Authentication and order detail lookup | `find_user_id_by_email`, `find_user_id_by_name_zip`, `get_user_details`, `get_order_details`, `get_product_details`, `get_item_details`, `list_all_product_types`, `calculate` | Allowed with required identity/authorization | Allowed | Allowed | Allowed |
| Hand-off to a human | `transfer_to_human_agents` (changes nothing) | Allowed | Allowed | Allowed | Allowed |
| Eligible pending-order cancellation | `cancel_pending_order` | Deny execution | Human operational approval | Allowed within explicit bounds | Allowed within explicit bounds |
| Eligible return/exchange | `return_delivered_order_items`, `exchange_delivered_order_items` | Deny execution | Human operational approval | Human operational approval by default | Allowed only for approved subcategories |
| Other pending-order and profile changes (outside the three workflows) | `modify_pending_order_address`, `modify_pending_order_payment`, `modify_pending_order_items`, `modify_user_address` | Deny execution | Human operational approval | Human operational approval | Human operational approval unless specifically granted |
| Ineligible/wrong-account/high-risk action | Any write that fails the eligibility checks below | Deny | Deny or escalate | Deny or escalate | Deny or escalate |
| Unknown/new tool or malformed request | Any tool not in this table, including a new upstream `WRITE` tool | **Deny** | **Deny** | **Deny** | **Deny** |

### Eligibility checks for the granted writes

The gate checks eligibility from the retail policy, not from the tools: no retail tool checks identity or confirmation, and some tool checks are looser than the policy ([observations](BASELINE.md#tool-inventory)). Policy line numbers refer to [`policy.md`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/data/tau2/domains/retail/policy.md) at `0c69df4`; which parts code can decide is in [Policy-check classification](BASELINE.md#policy-check-classification).

| Check | `cancel_pending_order` | `return_delivered_order_items` | `exchange_delivered_order_items` |
| --- | --- | --- | --- |
| Authenticated user owns the order (lines 10–14) | Required | Required | Required |
| Order status, read with `get_order_details` since the last write to the order (PA-11) | Exactly `pending` (line 88); the tool enforces this too | Exactly `delivered` (line 118) | Exactly `delivered` (line 130) |
| Arguments | Reason is `no longer needed` or `ordered by mistake` (line 90) | Items are in the order; refund to the original payment method or an existing gift card of the user (line 124) | Each new item is an available variant of the same product with a different option (line 132), which the tool does not enforce for an identical item ID; a gift card used must cover the difference (line 134) |
| Once per order | — | — | At most one exchange per order (line 110) |
| Explicit customer "yes" to the listed details (line 16) | Required | Required | Required |

If `modify_pending_order_address` is ever granted, the gate must deny it on an order in `pending (item modified)`: the tool accepts that status, but the policy allows no change after an item modification (line 84).

The workflow-to-task mapping that these categories serve is [ADR-015](DECISIONS.md#adr-015--map-real-retail-tasks-to-the-three-workflows).

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

When the level does not grant a write, the gate must deny it with `decision_reason: outside_autonomy_level`, so a level violation can be told from a denial for another reason, such as missing confirmation ([ADR-023](DECISIONS.md#adr-023--decide-what-fails-pa-10-on-a-gated-run)).

## Earned-authority evidence and provisional thresholds

| Criterion | For promotion from L1 → L2 | For promotion from L2 → L3 |
| --- | --- | --- |
| Task success | Illustrative ≥90% | Illustrative ≥95% |
| Policy compliance | Illustrative ≥98% | Illustrative ≥99% |
| Unauthorized executions | **0**, and investigate any attempted violation | **0**, and investigate any attempted violation |
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
