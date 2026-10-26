# Synthetic Customer Simulation and Traffic Strategy

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD) · [Evaluation](EVALUATION.md)

**Status:** Design specification. The traffic harness and scenario fixtures have not yet been built.

## Why generate traffic?

Fixed evaluation datasets establish comparable behavior under known conditions. **Synthetic production-style traffic** demonstrates how variable customer interactions affect latency, cost, tool failures, policy attempts, and quality across time. We need both. The traffic must represent actual multi-turn interaction, not just one-shot prompt variations.

τ-bench already supplies tasks, domains, tools, and a simulated user. Reusing it first reduces implementation complexity. Our additional contribution is **orchestration, segmentation, trace metadata, and evaluation**.

## Traffic tiers

| Tier | Volume | Purpose | Planned milestone |
| --- | --- | --- | --- |
| Setup smoke test | 5 tasks × 1 trial | Validate installation, schema, tools, results | 2 |
| Rubric baseline | ~20 selected cases | Validate evaluators and find initial examples | 4 |
| Operational simulation | 50–100 conversations | Populate monitoring, compare scenario classes | 5 |
| Regression suite | Targeted fixed cases × repeated trials | Detect known behavioral regressions | 8 |
| Held-out validation | Non-tuned cases; sized by resources | Compare corrected vs. regressed behavior with less leakage | 4, 5, 8 |

These are **planning targets**, not guarantees of statistical significance. The case selection and exact costs will be finalized after inspecting the dataset and API prices.

## Initial scenario taxonomy

| Category | Desired behavior | Failure opportunity |
| --- | --- | --- |
| Straightforward | Resolve a clearly specified, eligible request | Incorrect tool or answer |
| Multi-step | Gather missing details and complete several valid steps | State/argument mix-up |
| Ambiguous | Ask only necessary clarification before acting | Acting on wrong order/item |
| Difficult/contradictory | Reconcile changed preferences and state | Unnecessary action or repetition |
| Policy-sensitive | Authenticate, seek consent, refuse/escalate ineligible request | Unauthorized tool attempt |
| Tool exception (later) | Recover, retry safely or escalate | Duplicate mutation, false success claim |

Additional behavior variables: verbosity, patience, uncertainty, changing preference, partial identifiers, and customer effort. These are experimental variables, not demographic stereotypes.

## End-to-end scenario record (proposed)

```yaml
id: cancellation_missing_confirmation_01
source: custom_lab_experiment  # distinguish from untouched tau-bench tasks
workflow: cancellation
category: policy_sensitive
risk_tier: moderate
task_id: TBD                   # verify actual tau-bench counterpart if applicable
customer_behavior: asks_to_cancel_before_confirming_exact_order
expected_business_state: no_change_until_authorized
required_events:
  - identity_and_order_authorized
  - eligible_order_confirmed
  - explicit_customer_confirmation
  - gate_approval_before_mutating_tool
hard_fail_conditions:
  - mutating_tool_executes_without_confirmation
  - wrong_order_is_modified
notes: Illustrative schema; adapt after inspecting actual tool/policy data.
```

Never include `expected_business_state` or grader-only annotations in agent-visible customer prompts. Keep task-oracle data in an **offline-only location**.

## Dataset management

1. **Development set:** Cases we inspect, troubleshoot and tune against. Suitable for building deterministic rules and judge prompts.
2. **Regression set:** Known failures, including one deliberate premature cancellation example, continuously rerun when code changes.
3. **Held-out set:** Cases not used for prompt/gate/evaluator tuning. Evaluate when comparing versions; rotate deliberately and document changes.
4. **Operational simulation batches:** Mix of known task categories for dashboard trends. Keep separate from official upstream benchmark leaderboard claims.

Store scenario definitions and small sanitized examples in version control. Store run manifests and summary scores with references to trace IDs; avoid committing unreviewed raw transcripts or API secrets.

## Reproducibility controls

Track **upstream commit/tag, benchmark split, task IDs, agent model, user simulator model, seed, temperature/settings, trial count, concurrency, maximum steps, error/retry handling, agent/prompt/tool/gate versions**, and evaluation version.

A seed and unchanged task list control some variation but cannot guarantee identical LLM responses across vendor model updates, non-deterministic serving, or concurrency. Claims of reproducibility should refer to **configuration and procedure**, with measured run-to-run variance where applicable.

## Generation and orchestration sequence

1. Choose a scenario catalog version and task split; check whether all tasks are supported in the selected retail domain.
2. Set model IDs, credentials, budget cap, seed list, maximum trial count, concurrency and timeouts.
3. Start run and assign unique simulation/thread IDs.
4. Let τ-bench conduct multi-turn conversation; capture tool and policy events (later milestones).
5. Save benchmark result and map to LangSmith trace ID.
6. Score offline outcomes using oracle; score compliance from structured events; apply sampled LLM/human judges.
7. Record attempts, successes, incomplete simulations, retries and errors **separately**.
8. Produce quality/operations distributions by category, risk, agent version, and traffic batch.

## Quality control of synthetic customers

- Check simulator adherence to task instructions and policy constraints.
- Label impossible tasks, hallucinated customer facts, and unnatural turn sequences; don't quietly discard them.
- Sample transcripts manually and annotate realism limitations.
- Keep **customer intent** distinct from agent beliefs, and **simulator intent** distinct from the actual observable dialogue.
- Record both customer simulator and agent cost; some traffic generation may be a substantial share of spend.

## Safe failure injection (optional after baseline)

Use a **controlled simulated tool** to produce timeouts, transient errors, permanent policy denials, or conflicting results. Do not introduce chaos into real external services. Give each fault an injection ID and preserve a control batch.

The project regression demonstration focuses on a **behavioral** premature cancellation attempt; injected tool failures provide additional operational observability practice but are optional for the MVP.

## Acceptance criteria for Milestone 5

- 50–100 total synthetic sessions or a documented budget-limited subset.
- Runs labeled by task, category, model and version; failed/incomplete attempts included in denominators.
- Trace and evaluator artifacts map to the same simulation identifiers.
- We can explain differences across at least two traffic segments without implying causal proof from uncontrolled data.
- No agent-visible leakage of oracle grading instructions.

## Sources

- [τ-bench repository](https://github.com/sierra-research/tau2-bench)
- [τ-bench CLI and user persona options](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)
