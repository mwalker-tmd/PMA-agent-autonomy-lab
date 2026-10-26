# Office-Hours Teaching Demonstration — Facilitator Runbook

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD) · [Project brief](PROJECT_BRIEF.md)

**Status:** Proposed teaching design, not a record of a completed demo. Actual URLs, screenshots, scores and commands will be filled in after implementation.

## Session theme

**From tool use to earned trust: operating an AI support agent in production-like conditions.**

This follows the earlier RAGAS pipeline evaluation session. The audience now sees quality at the **agent workflow and business-outcome level**, including tool calls, customer consent, policy compliance, model behavior and authorization decisions.

## Opening narrative (suggested)

A new support employee wouldn't immediately receive authority to cancel customer orders without supervision. The employee starts with limited access, demonstrates good judgment and follows policy, and gradually earns carefully scoped permissions. **We should apply the same evidence-driven standard to an AI support agent—but enforce its permissions in code.**

The demo answers four questions in sequence:

1. What did the agent do?
2. Was it correct and compliant?
3. How would we know if its behavior changed?
4. Who decides how much it may do independently?

## Suggested 30–35-minute teaching outline

| Segment | Suggested duration | Live artifact | Primary lesson |
| --- | ---: | --- | --- |
| 1. Customer request | 4 min | One multi-turn retail scenario | Agents do more than produce text |
| 2. Observability | 5 min | LangSmith thread, tool and model spans | Reconstruct why actions happened |
| 3. Evaluation | 5 min | Task outcome vs. policy and experience scores | A correct state alone isn't enough |
| 4. Simulated traffic | 4 min | 50–100 session dashboard and segment breakdown | Monitoring trends and denominators |
| 5. Earned autonomy | 5 min | Level 1 (Supervised) approval versus Level 2 (Trusted within bounds) execution | Evidence-based, code-enforced authority |
| 6. Regression and restriction | 7 min | Release comparison, denied attempt, regression trace | Detect → investigate → restrict → correct |
| 7. Wrap-up | 3 min | Promotion packet and discussion | Product ownership of risk tolerance |

Adjust depth for a shorter session; the order of concepts matters more than the exact timing.

## Detailed demonstration script

### 1 — Observe the agent in action

Run a synthetic customer request (e.g. eligible cancellation, selected from actual retail tasks). Show clarification, eligible order selection, customer confirmation and the resulting tool output. Ask: **Did the customer get what they wanted? What else do we need to know?**

**Evidence needed:** run manifest, source task, complete conversation, final simulated state.

### 2 — Use observability to explain behavior

Open LangSmith by the same simulation ID. Walk from top-level conversation to model/tool spans and corresponding gate/approval events. Ask: **Could we determine what happened if the customer complained tomorrow?**

**Evidence needed:** trace URL, thread ID, version metadata, tool arguments/results (sanitized), error path example.

### 3 — Evaluate both outcome and conduct

Show how the oracle verifies a correct final state and independent evaluators verify policy. Contrast a compliant success against a *controlled example* that yields the requested result without required confirmation. Ask: **Does a high task-success rate grant the agent authority?**

**Evidence needed:** scoring rubric, deterministic evaluator output, sampled judge output, human review example and sources.

### 4 — Monitor synthetic production traffic

Show the workload mix, run count, task success rate, tool errors, latency/cost and unsafe attempts. Distinguish realistic-ish synthetic operations from a real production reliability claim. Ask: **Which signal would you watch after a release?**

**Evidence needed:** actual generated traffic, dashboards, counts/denominators and metadata.

### 5 — Earn autonomy

Demonstrate the same customer-confirmed eligible action under Level 1 Supervised (human employee approval needed) and Level 2 Trusted within bounds (executes within its grant). Show that customer authentication and consent stay required in **both** cases. Ask: **Who is accountable for approving the grant?**

**Evidence needed:** policy matrix, gate event, promotion review record and current action grants.

### 6 — Introduce a controlled regression

Change the agent version/prompt so it sometimes attempts cancellation before customer confirmation. Run a matched scenario batch. The gate denies the attempted action. Detect the pattern, inspect one failed trace, restrict that action, create a regression test, correct the agent, and compare new results. Ask: **Was the system safe because the agent behaved correctly, or because the gate worked?**

**Evidence needed:** deliberately bad version manifest, matched run data, real trace, denial reason, restriction test, corrected version and report.

### 7 — Close on product judgment

Return to: **Which actions can the agent perform reliably, what evidence supports that assessment, and what controls protect customers when it fails?** Emphasize verified resolution rather than maximizing automation.

## Pulse-check questions

Use a few rather than all of these in one session:

- “If the final order state is correct but customer confirmation was skipped, did the agent pass?”
- “Is an attempted unauthorized tool call a system failure if the gate blocks it?”
- “What can traces explain that aggregate metrics cannot?”
- “Would 95 successes out of 100 be enough to authorize refunds or account transfers? Why or why not?”
- “Which metrics belong to Product, which to Engineering, and who owns the autonomy decision?”
- “Why should production-style evaluation never assume access to the simulator's hidden answer key?”

## Presentation assets planned

- Simple architecture diagram from [ARCHITECTURE.md](ARCHITECTURE.md).
- Permission matrix from [AUTONOMY.md](AUTONOMY.md).
- Three version scorecards (v1.0 baseline, v1.1 deliberate regression, v1.2 correction) from **measured** runs.
- Two representative trace links, one normal and one failed.
- A dashboard screenshot showing quality and operational signals separately.
- A one-page autonomy review packet and a tiny regression test excerpt.

Any illustrated data must be marked **hypothetical** until real runs exist. Never present fabricated scores as simulation results.

## Demo-readiness checklist

- [ ] Local repo and upstream τ-bench checkout pinned to known versions.
- [ ] Synthetic environment and API keys configured securely; account usage limits verified.
- [ ] Known-good scenario IDs and simulation manifests selected and available.
- [ ] LangSmith project accessible; threads, spans and dashboards load on presentation display.
- [ ] Baseline/corrected/regression result comparison contains actual measured numbers and denominators.
- [ ] Gate rules and current autonomy level visible, with a clear distinction between customer consent and employee approval.
- [ ] Restriction flow verified: denial changes no underlying business state.
- [ ] Offline demo assets captured and stored safely with version/date labels.
- [ ] Presenter knows the one-sentence takeaway and which additional details to skip if time runs short.

## Fallback sequence

1. If the **LLM provider** is unavailable, replay pre-recorded synthetic conversations and saved run outputs with true provenance.
2. If **LangSmith** is unavailable, display saved sanitized trace excerpts/screenshots and the corresponding evaluation manifest.
3. If the **dashboard** fails to load, use a static scorecard generated from actual runs.
4. If the regression is not reproducible live due to stochasticity, show saved repeated-trial evidence and run the deterministic gate regression unit test.
5. Preserve the lesson even when a service fails: **risk controls must operate independently of monitoring and evaluation services.**

## Suggested follow-on lessons

Optional, not part of the planned milestones: comparing two model providers on the same scenarios; switching to an OpenTelemetry-compatible alternative; explaining evaluator drift, threshold calibration and confidence intervals; adding voice/knowledge domains; or piloting a separate, unrelated agent application.
