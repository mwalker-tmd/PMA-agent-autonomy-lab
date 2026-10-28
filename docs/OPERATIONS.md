# Monitoring, Incident Response and Recovery

[Home](../README.md) · [Master plan](../DEVELOPMENT.md) · [Evaluation](EVALUATION.md) · [Autonomy](AUTONOMY.md)

**Status:** Planned operational specification. Actual dashboards, alert delivery and measurements are Milestones 7–8.

## Operational questions

**Monitoring:** Are errors, latency, expenses, unresolved requests, or unsafe attempts rising?  
**Observability:** Which conversation, tool sequence, model/prompt version, and policy decision explain the change?  
**Evaluation:** Was the customer objective achieved and was the agent compliant?  
**Governance:** Does the observed evidence justify maintaining, promoting, or restricting the agent's authority?

These are related disciplines but different responsibilities. Monitoring can be fast and approximate; permission enforcement cannot wait for asynchronous quality evaluation.

## LangSmith project and data settings

Recorded 2026-10-28 for [Milestone 3](../DEVELOPMENT.md#milestone-3--integrate-langsmith-observability) step 3.1. Values come from the LangSmith API for the lab's API key unless a row says otherwise.

| Setting | Value | Source |
| --- | --- | --- |
| Organization | `Personal` (single-seat personal organization) | `GET /api/v1/orgs/current/info` |
| Workspace | `Workspace 1` (the organization's only workspace; the plan allows one) | `GET /api/v1/workspaces` |
| Region (endpoint) | GCP US, `us-central1`: API `https://api.smith.langchain.com`, UI `https://smith.langchain.com` | `LANGSMITH_ENDPOINT`; [LangSmith Cloud regions](https://docs.langchain.com/langsmith/cloud) |
| Plan | Developer (`plan_tier: developer_10_2026`) | `GET /api/v1/orgs/current/info` |
| Project | `agent-autonomy-lab-synthetic` by default; `LANGSMITH_PROJECT` overrides it, so each cohort can use its own project. LangSmith creates the project on the first trace. | `entrypoints/settings.py` |
| Retention tier | Base (`shortlived`), 14 days, for this project and as the organization default; no project is set to extended | `GET /api/v1/sessions/{id}` (`trace_tier`), `GET /api/v1/orgs/ttl-settings` |
| Public trace sharing | Allowed (`public_sharing_disabled: false`); no trace is shared | `GET /api/v1/orgs/current/info`, `GET /api/v1/workspaces/current/shared` |
| Use of trace data for model training | Not exposed by the API and not covered in the LangSmith docs pages consulted; not verified | — |

**Retention and evidence links.** Base-tier traces expire 14 days after they are recorded. After that, a trace is no longer available in the UI or API, and its inputs and outputs are deleted within a day ([data retention](https://docs.langchain.com/langsmith/administration-overview)). So a trace URL or trace ID cited as evidence stops working after 14 days. Every trace cited as milestone evidence is therefore also captured as a screenshot committed under `reports/`; the trace ID stays in the record so a live trace can still be matched while it exists. Some actions upgrade a trace to extended retention (180 days) and cost more: online evaluators and automation rules do so by default, and API feedback does when `extend_trace_retention=true` is passed. Datasets keep their examples indefinitely. Account for these upgrades when Milestones 4 and 7 add evaluators and rules.

**Privacy.** All lab traffic is synthetic τ-bench data tagged `traffic_type=synthetic`, so the project holds no real customer data. Public sharing is allowed by the plan; a public link is visible to anyone who has it, so share traces only deliberately and unshare them under **Settings → Shared URLs**. Never put the API key or `.env` contents in a trace.

**Smoke trace.** `uv run --env-file .env python -m agent_autonomy_lab.entrypoints.smoke_trace` sends one `smoke_trace` run tagged `smoke` with `traffic_type=synthetic` and prints its trace ID. The first run, on 2026-10-28, recorded trace `01a18117-e679-7d13-82cd-cfb6adee0b4d` (status `success`) in `agent-autonomy-lab-synthetic`; the screenshot is `reports/m3-traces/screenshots/m3-01-smoke-trace.png`.

## Metric dictionary (target)

| Metric | Numerator/aggregation | Denominator or scope | Owner / action |
| --- | --- | --- | --- |
| Conversation volume | Started and completed sessions | Time window | Operations / workload context |
| Completion rate | Completed sessions | Started sessions | Engineering / failure investigation |
| Task success rate | Eligible tasks scored as [task success](EVALUATION.md#initial-scorecard--evidence-matrix) | Completed, scored eligible tasks | PM/product / outcome quality |
| Critical policy failure | Critical violations | Applicable evaluated cases | Governance / restrict when warranted |
| Unauthorized attempts | Gate-invalid attempted tools | Sessions and/or protected tool attempts | Governance / inspect and restrict |
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

- Task success rate and policy compliance separately, sampled qualitative scores.
- Unauthorized attempts vs. unauthorized executions, gate denials by reason.
- Escalation and human approval rates, current autonomy level/grants.
- Drill-down to concrete traces and gate events.

**View 3 — Version comparison / release review**

- The planned demonstration versions (v1.0 baseline, v1.1 deliberate regression, v1.2 correction) compared across the same scenario mix.
- Show sample size and denominators, not just percentages; include data freshness.
- Highlight any critical violations even if overall task success increases.

[LangSmith dashboards](https://docs.langchain.com/langsmith/dashboards) provide views for trace counts, feedback scores, latency, tokens, and costs, with custom filtering/grouping. Some desired calculations or alerts may require derived events/scripts rather than built-in dashboard controls; verify account capabilities during Milestone 7.

## Signals and response (initial proposals)

| Signal | Example trigger (proposed) | Immediate action |
| --- | --- | --- |
| Unauthorized execution | Any event | Restrict affected tool permission; investigate urgently |
| Premature cancellation attempt | Any high-confidence occurrence or sustained uptick | Inspect exact gate reason; consider temporary restriction |
| Missing authentication/confirmation evidence | Tool request with required evidence absent | Gate denies synchronously; open investigation if repeated |
| Tool failure cluster | Error rate outside baseline in comparable traffic | Inspect failing tool and dependency; possibly disable tool |
| Resolution degradation | Significant shift from matched baseline | Stratify by version/intent, evaluate confidence, initiate rollback review |
| Unexpected cost/latency | Budget/SLO trend exceeded | Identify model/tool calls and retry loops; optimize without weakening safety |

Thresholds for noncritical trends must be chosen after obtaining actual baseline distributions. Small-sample noise and scenario-mix changes should not masquerade as regressions.

## Online evaluation timing and sampling

Multi-turn LangSmith evaluation may run after thread inactivity rather than after every tool call. LangSmith currently documents a thread idle setting with a minimum of two minutes. That makes thread-level judges useful for **retrospective quality signals**, not synchronous action permissions. Restrict dangerous actions at the authorization gate immediately and use async evaluators to study broader behavior. For cost control, sample qualitative judges and run critical deterministic checks exhaustively where practical.

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
affected_action: cancel_pending_order
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

## Verification checks for Milestones 7 and 8

These checks support the canonical Acceptance Criteria for [Milestone 7](../DEVELOPMENT.md#milestone-7--build-operational-monitoring) and [Milestone 8](../DEVELOPMENT.md#milestone-8--introduce-and-diagnose-regressions) in `DEVELOPMENT.md`.

- Alert/signal can be drilled down to at least one relevant trace and gate event.
- The dashboard shows actual outcome metrics and separate process-compliance metrics.
- A forbidden cancellation **attempt** is detectable and harmless if the gate works.
- A restriction prevents the next protected execution without relying on dashboard freshness.
- Comparison report includes real denominators, manifests, model/version changes, and a reproducible regression test.
