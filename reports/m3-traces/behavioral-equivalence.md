# Behavioral equivalence on a deterministic replay

Milestone 3, [#77](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/77). Recorded on October 29, 2026 (America/Denver). Every value below comes from the runs listed in [`manifest.yaml`](manifest.yaml) (`deterministic_replay_task_0`). How the replay works is in [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#deterministic-replay-behavioral-equivalence).

**Result: enabling tracing changed nothing.** Retail task `0` was run once live and recorded as a cassette of 15 model responses. It was then replayed twice: with tracing off (upstream's `LLMAgent`, unwrapped) and with tracing on (the traced lab agent, sent to LangSmith). Both replays matched the cassette request for request: every request each run sent equals the recorded one, and all 15 responses were served. Their outcomes are identical in every compared field, and both equal the recorded live run's.

## What was compared

`compare_outcomes` with the tracing-off replay as reference (exit 0):

| Field | Tracing on | Recorded live run | Value |
| --- | --- | --- | --- |
| Agent messages | equal | equal | 11 (10 model replies and the fixed opener) |
| Tool calls, name and arguments | equal | equal | `find_user_id_by_name_zip`, `get_order_details`, `get_user_details`, `get_product_details` ×2, `exchange_delivered_order_items` |
| Full transcript (every role, content, tool call, tool result) | equal | equal | 22 messages, 0 tool errors |
| Final database hash, from the live environment | equal | equal | `a109be17…3ac25a` |
| Termination | equal | equal | `user_stop` |
| Reward | equal | equal | 1.0 |
| Reward breakdown | equal | equal | `DB` 1.0, `NL_ASSERTION` 1.0 |
| Reward details (database check, action checks) | equal | equal | 5 of 5 reference actions matched |

Token counts are equal too (agent 10 calls, 50,612 prompt and 1,046 completion tokens; user 5 calls, 5,404 and 483), because they come from the recorded responses. Durations are not compared: a replay takes 0.1 s against the live run's 39.3 s.

The comparison is strict. The replay fails on the first request that differs from the recording in any field of its body: system prompt, tool schemas, conversation, or model settings. So the traced agent's equal outcome is not luck in sampling: it sent the same 10 requests as the untraced agent. The offline test `tests/test_deterministic_replay.py` checks the strictness: an agent whose prompt adds one sentence fails the replay on `body.instructions`.

## The traced replay's thread

Read back through the LangSmith API after the run:

- one thread (`2030b1fd-a6a0-5ee0-bd02-f5a22e17212b`) with one `conversation` root (`01a18aa8-2d1e-7ef9-8c88-250ca1b31a16`);
- 22 runs, all children parented to the root: 10 agent `model_call` spans and 5 simulated-user ones (the cassette's 15 responses), and 6 `tool_call` spans (the run's 6 tool calls);
- every run carries the same `thread_id` and `simulation_id`; the root is labeled `traffic_type: synthetic`, `experiment_class: custom_lab_experiment`, `autonomy_level: n/a`;
- reward feedback on the root: `reward`, `reward_db`, `reward_nl_assertion`, all 1.0;
- no error spans, and no API key value in any payload.

All 10 traced turns sent the same prompt, tool schema, and model-argument hashes as upstream's `llm_agent` reference, the same values as the M3-05 live run.

Screenshot (trace URLs expire with the project's 14-day retention): [the thread](screenshots/m3-07-thread.png), showing the thread ID, one conversation turn, the 15 `model_call` and 6 tool spans in order, the three reward feedback items, tokens (57.55K, equal to the run's 50,612 + 1,046 + 5,404 + 483) and cost ($0.00234481), and the conversation with each tool call's arguments and result.

## Findings

1. **No judge model ran.** The cassette holds every model response of the run, but task `0` has no NL assertions ("No nl_assertions to evaluate") and no communicate checks, so the benchmark called no judge. The recording and replay transports see judge calls the same way as the others, because they sit below every LiteLLM call. A task with NL assertions would exercise that path.
2. **`reasoning_effort` and `seed` never reach OpenAI.** The recorded request bodies have no `reasoning` and no `seed` field. With LiteLLM 1.81.11, `gpt-6-luna` is missing from LiteLLM's model map, so LiteLLM treats `reasoning_effort` as unsupported and τ-bench's `drop_params` removes it. A probe without `drop_params` raises `UnsupportedParamsError`, and a model in the map (`gpt-5.4-mini`) does get `reasoning.effort`. The Responses bridge sends no `seed` for any model. Every response reports `effort: medium`, the model's default. So the Milestone 2 baseline's declared `reasoning_effort: medium` matches what ran, but only because it is the default. A different declared effort would have been silently ignored. The `seed` in the baseline settings only seeds τ-bench's own randomness. The model sees no seed, which is one more reason live runs do not repeat.
3. **Replays recompute cost.** LiteLLM prices each replayed response from its recorded usage, so the traced replay's LangSmith cost ($0.0023448) is the recorded run's estimate, not a new charge. A replay makes no provider call: provider keys are replaced by a placeholder for its duration.
