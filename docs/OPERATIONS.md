# Monitoring, Incident Response and Recovery

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD) · [Evaluation](EVALUATION.md) · [Autonomy](AUTONOMY.md)

**Status:** Planned operational specification. Actual dashboards, alert delivery and measurements are Milestones 7–8.

## Operational questions

**Monitoring:** Are errors, latency, expenses, unresolved requests, or unsafe attempts rising?  
**Observability:** Which conversation, tool sequence, model/prompt version, and policy decision explain the change?  
**Evaluation:** Was the customer objective achieved and was the agent compliant?  
**Governance:** Does the observed evidence justify maintaining, promoting, or restricting the agent's authority?

These are related disciplines but different responsibilities. Monitoring can be fast and approximate; permission enforcement cannot wait for asynchronous quality evaluation.

## Metric dictionary (target)

| Metric | Numerator/aggregation | Denominator or scope | Owner / action |
| --- | --- | --- | --- |
| Conversation volume | Started and completed sessions | Time window | Operations / workload context |
| Completion rate | Completed sessions | Started sessions | Engineering / failure investigation |
| Verified resolution rate | Oracle-verified successful eligible tasks | Completed, scored eligible tasks | PM/product / outcome quality |
| Critical policy failure | Critical violations | Applicable evaluated cases | Governance / restrict when warranted |
| Unauthorized action attempts | Gate-invalid attempted tools | Sessions and/or protected tool attempts | Governance / inspect and restrict |
| Unauthorized executions | Forbidden tools that **executed** | All protected executions | **Critical incident** |
| Gate deny rate | Denied actions | All protected action attempts | Engineering + policy owner / investigate, not automatically bad |
| Human approval rate | Actions held for human approval (`PENDING_APPROVAL`) | Eligible protected action attempts | Operations / supervision load |
| Escalation rate | Escalated sessions | Completed sessions | Product/operations / contextual |
| Tool error rate | Failed tool invocations | Total tool invocations | Engineering / dependency repair |
| Conversation latency | p50/p95 wall-clock duration | Completed sessions | Engineering / SLO tracking |
| Model token use / cost | Usage sum/avg, dollars where available | Session, task type, agent version | PM/engineering / cost-quality tradeoff |

Avoid treating every denied action as an agent failure: some are **correct** refusals. Distinguish user ineligible request, agent invalid attempt, and gate behavior in underlying event categories.

## Dashboard design

**View 1 — Reliability and cost**

- Volume and completion trends, tool errors, latency distributions, token and cost trends.
- Breakdowns by agent model/version, task class, traffic batch and error category.
- Explicit incomplete/failed-to-run counts; synthetic-only banner.

**View 2 — Customer and policy outcomes**

- Verified resolution and policy compliance separately, sampled qualitative scores.
- Attempted vs executed unauthorized mutations, gate denials by reason.
- Escalation and human approval rates, current autonomy level/grants.
- Drill-down to concrete traces and gate events.

**View 3 — Version comparison / release review**

- v1.0 healthy, v1.1 regressed, v1.2 corrected results across the same scenario mix.
- Show sample size and denominators, not just percentages; include data freshness.
- Highlight any critical violations even if overall task success increases.

[LangSmith dashboards](https://docs.langchain.com/langsmith/dashboards) provide views for trace counts, feedback scores, latency, tokens, and costs, with custom filtering/grouping. Some desired calculations or alerts may require derived events/scripts rather than built-in dashboard controls; verify account capabilities during Milestone 7.

## Signals and response (initial proposals)

| Signal | Example trigger (proposed) | Immediate action |
| --- | --- | --- |
| Forbidden mutation executed | Any event | Restrict affected tool permission; investigate urgently |
| Premature cancellation attempt | Any high-confidence occurrence or sustained uptick | Inspect exact gate reason; consider temporary restriction |
| Missing authentication/confirmation evidence | Tool request with required evidence absent | Gate denies synchronously; open investigation if repeated |
| Tool failure cluster | Error rate outside baseline in comparable traffic | Inspect failing tool and dependency; possibly disable tool |
| Resolution degradation | Significant shift from matched baseline | Stratify by version/intent, evaluate confidence, initiate rollback review |
| Unexpected cost/latency | Budget/SLO trend exceeded | Identify model/tool calls and retry loops; optimize without weakening safety |

Thresholds for noncritical trends must be chosen after obtaining actual baseline distributions. Small-sample noise and scenario-mix changes should not masquerade as regressions.

## Online evaluation timing and sampling

Multi-turn LangSmith evaluation may run after thread inactivity rather than after every tool call. LangSmith currently documents a thread idle setting with a minimum of two minutes. That makes thread-level judges useful for **retrospective quality signals**, not synchronous action permissions. Restrict dangerous actions at the tool gate immediately and use async evaluators to study broader behavior. For cost control, sample qualitative judges and run critical deterministic checks exhaustively where practical.

## Incident handling runbook

**Case: a new prompt causes attempts to cancel orders before customer confirmation.**

1. **Detect:** Quality metric/alert shows increased gate-denied `missing_customer_confirmation` attempts. Record alert ID, run period and affected agent/prompt version.
2. **Scope:** Confirm traffic is comparable; estimate counts by scenario/task type, model version, risk level and time window.
3. **Inspect:** Open traces and ordered tool/gate events. Separate model intent, gate decision, actual business mutation and customer response.
4. **Protect:** Ensure existing gate blocked mutations. If risk or confidence warrants, change the active policy to require supervised approval for the cancellation category and verify enforcement immediately.
5. **Root-cause analysis:** Examine prompt/tool schema changes; reproduce with task IDs and run manifest. Consider whether a simulator or evaluator issue could produce a false signal.
6. **Regression coverage:** Add the observed trace/scenario to the regression suite; assert no mutation before confirmation and correct enforcement when agent attempts one.
7. **Correct:** Change prompt/model/agent/tool policy as appropriate. Keep gate guardrails in place while validating.
8. **Verify:** Compare v1.0/v1.1/v1.2 using matched and held-out scenarios, repeated trials, and sampled human review. Report uncertainty.
9. **Decide:** Reviewer approves **maintain/restrict/restore**, with precise action grants, evidence and reasons. Promotion is never automatic because scores improved.
10. **Learn:** Record remediation action, owner, due date, and follow-up checks in the incident review.

## Incident record template (planned)

```yaml
incident_id: TBD
traffic_type: synthetic
detected_at_utc: TBD
signal: unauthorized_cancellation_attempts
agent_version: TBD
prompt_version: TBD
policy_version: TBD
affected_action: cancel_order
scope: TBD
attempts_detected: TBD
unauthorized_executions: TBD
trace_ids: []
protective_action: TBD
root_cause: TBD
regression_test_id: TBD
corrected_agent_version: TBD
verification_report: TBD
human_reviewer: TBD
permission_decision: maintain | restrict | restore
follow_up_owner: TBD
follow_up_due: TBD
```

## Verification for Milestones 7 and 8

- Alert/signal can be drilled down to at least one relevant trace and gate event.
- The dashboard shows actual outcome metrics and separate process-compliance metrics.
- A forbidden cancellation **attempt** is detectable and harmless if the gate works.
- A restriction prevents the next protected execution without relying on dashboard freshness.
- Comparison report includes real denominators, manifests, model/version changes, and a reproducible regression test.
