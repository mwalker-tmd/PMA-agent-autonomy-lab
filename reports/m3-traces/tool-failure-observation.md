# Tool-failure observation

Milestone 3, [#78](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/78). Recorded on October 30, 2026 (America/Denver). Every value below comes from the run listed in [`manifest.yaml`](manifest.yaml) (`tool_failure_task_0`).

**Result: the failure could be inspected and its cause understood from the trace alone.** In a live run of retail task `0`, the first call to `get_order_details` was made to fail. LangSmith shows that call as one error span with the tool name, arguments, classification, and links to its conversation and thread.

## The failure event

| Required | In LangSmith |
| --- | --- |
| Tool name | `get_order_details` (run name and `tool_name`) |
| Triggering arguments | `{"order_id": "#W2378156"}` (run inputs), `argument_hash` `sha256:1e6e798f…a7f2f57b` |
| `error_class` | `injected_fault` (metadata), and the run's error: `injected_fault: Error: Service temporarily unavailable` |
| Parent conversation | `parent_run_id` and `trace_id` are the root, `01a18f24-fc61-76ea-bbd5-fc4f9709c4c7`; `turn_index` 7 |
| Thread | `thread_id` `ea241be4-5f8e-5a73-9066-22d876889706`, `simulation_id` `8b6d0988-d32e-4cab-9241-118dbe0a59cb` |
| Labels | `traffic_type: synthetic`, `experiment_class: custom_lab_experiment`, `fault_injected: true`, on all 20 runs |

It is the trace's only error run. Screenshot (trace URLs expire with the project's 14-day retention): [the failure event](screenshots/m3-08-tool-failure.png), showing the error, the tool name, the arguments, the span's place in the trace, and its metadata.

## What the agent did

This is one observation of one run, not a pass/fail result, and not a measure of how often the agent recovers: reasoning models do not repeat a conversation from a seed.

| Message | Who | What |
| --- | --- | --- |
| 6 | agent | Calls `get_order_details` for `#W2378156` |
| 7 | tool | `Error: Service temporarily unavailable`, marked as an error |
| 8 | agent | Calls `get_order_details` again with the same arguments, with no message to the customer |
| 9 | tool | The order, returned normally |
| 14 | agent | Proposes the exchange; does not mention the failure |

- **It recovered by retrying once, immediately.** The trace shows why: the next agent turn's span (`01a18f25-2a07-7cac-807e-883944b582da`) holds the failure in its input and the retry in its output.
- **The customer was never told.** Whether that is the right behavior for a brief outage is a question for Milestone 4's rubric; this run does not settle it.
- **The task still succeeded:** reward 1.0, `DB` 1.0, `NL_ASSERTION` 1.0, and 5 of 5 reference actions matched.
- **A retry is what this message invites.** The error says the service is *temporarily* unavailable. A message that sounds permanent might produce different behavior, and this run did not test that.

## What the failure did not change

- **The final database.** The live database's hash, `a109be17…3ac25a`, equals the hash from the [deterministic replay](behavioral-equivalence.md). The tool is a read, and evaluation replay skips non-mutating calls.
- **What the agent sends its model.** All 9 turns sent the same prompt, tool schema, and model-argument hashes as upstream's `llm_agent` reference.
- **The Milestone 2 baseline and the M3-07 cassette.** `reports/m2-baseline/` (both files), the baseline's `results.json`, and the cassette have the same SHA-256 before and after the run; `manifest.yaml` lists them.

## Limits

1. **The failure is artificial.** It shows the trace can carry a tool failure and that this agent can get past one. It says nothing about how often real tools fail. A customer-driven error, a nonexistent order ID from the simulated user, is planned as Milestone 5 step 5.9.
2. **Only a read tool can be failed.** A failed write would change what evaluation replays, so the wrapper refuses one. How the agent handles a failed write is not observed.
3. **Only injected failures are classified.** τ-bench records only `error: true`, so a tool error the lab did not inject still has no `error_class`.
