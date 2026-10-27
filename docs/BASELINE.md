# τ-bench Baseline

[Home](../README.md) · [Master plan](../DEVELOPMENT.MD) · [Evaluation](EVALUATION.md)

**Status:** In progress (Milestone 2). Each section is added by its own ticket; machine-readable values live in [`reports/m2-baseline/manifest.yaml`](../reports/m2-baseline/manifest.yaml).

## Setup

Recorded on October 26, 2026 for [#38](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/38) (plan steps 2.2–2.4).

### Toolchain

| Item | Value |
| --- | --- |
| `uv` | 0.11.2 |
| Python used by τ-bench | 3.12.0 (uv-managed) |
| Upstream requirement | `>=3.12,<3.14` (`pyproject.toml`); `.python-version` is `3.12` |
| System Python | 3.14.3 — outside the upstream range, so not used |
| LiteLLM (from `uv.lock`) | 1.81.11 |

### Upstream checkout

τ-bench is cloned **beside** this repository at `../tau2-bench`, not inside it, so `git status` here never shows an embedded repository.

| Item | Value |
| --- | --- |
| Repository | <https://github.com/sierra-research/tau2-bench> |
| Branch | `main` |
| Commit | `0c69df456a96aa76a1162591574dad9c427a34d7` (October 24, 2026) |
| Tag | None at this commit; `git describe` gives `v2.0.0-62-g0c69df4` |
| Local modifications | None — `git status` in the clone is clean after `uv sync` and the smoke test |

To reproduce the checkout:

```bash
git clone https://github.com/sierra-research/tau2-bench.git ../tau2-bench
git -C ../tau2-bench checkout 0c69df456a96aa76a1162591574dad9c427a34d7
```

### Validation

`uv sync` installed the locked dependencies into `../tau2-bench/.venv` (ignored upstream):

```text
$ uv sync
Using CPython 3.12.0
Creating virtual environment at: .venv
Resolved 187 packages in 8ms
Installed 75 packages in 218ms
 + addict==2.4.0
 + aiohappyeyeballs==2.6.1
 + aiohttp==3.14.5
 + aiosignal==1.4.0
 + annotated-doc==0.0.4
 + annotated-types==0.7.0
 + anyio==4.14.2
 + attrs==25.4.0
 + certifi==2025.11.12
 + charset-normalizer==3.4.4
 + click==8.3.1
 + deepdiff==8.6.2
 + distro==1.9.0
 + docstring-parser==0.17.0
 + fastapi==0.121.2
 + fastuuid==0.14.0
 + filelock==3.25.2
 + frozenlist==1.8.0
 + fsspec==2025.10.0
 + h11==0.16.0
 + hf-xet==1.2.0
 + httpcore==1.0.9
 + httpx==0.28.1
 + huggingface-hub==1.1.4
 + idna==3.20
 + importlib-metadata==8.7.0
 + jinja2==3.1.6
 + jiter==0.12.0
 + jsonschema==4.25.1
 + jsonschema-specifications==2025.9.1
 + litellm==1.81.11
 + loguru==0.7.3
 + markdown-it-py==4.0.0
 + markupsafe==3.0.3
 + mdurl==0.1.2
 + multidict==6.7.0
 + numpy==2.3.5
 + openai==2.20.0
 + orderly-set==5.5.0
 + packaging==24.2
 + pandas==2.3.3
 + propcache==0.4.1
 + psutil==7.1.3
 + pydantic==2.13.5
 + pydantic-core==2.46.5
 + pygments==2.21.0
 + python-dateutil==2.9.0.post0
 + python-dotenv==1.2.4
 + pytz==2025.2
 + pyyaml==6.0.3
 + referencing==0.37.0
 + regex==2025.11.3
 + requests==2.34.2
 + rich==14.2.0
 + rpds-py==0.29.0
 + shellingham==1.5.4
 + six==1.17.0
 + sniffio==1.3.1
 + starlette==0.49.3
 + tabulate==0.9.0
 + tau2==2.0.0 (from file:///Users/mwalker-tmd/TAMARKDesigns/Clients/PMA%20Accelerator/demo/tau2-bench)
 + tenacity==9.1.2
 + tiktoken==0.12.0
 + tokenizers==0.22.1
 + toml==0.10.2
 + tqdm==4.67.1
 + typer==0.20.0
 + typer-slim==0.20.0
 + typing-extensions==4.15.0
 + typing-inspection==0.4.2
 + tzdata==2025.2
 + urllib3==2.8.0
 + uvicorn==0.38.0
 + yarl==1.25.1
 + zipp==3.23.0
```

Afterwards, `.venv/lib/python3.12/site-packages` held 75 `*.dist-info` directories, including `litellm-1.81.11.dist-info`. The lock lists 187 packages; the rest belong to optional extras (such as voice) that `uv sync` does not install by default.

The interpreter and data checks then passed:

```text
$ uv run python --version
Python 3.12.0
$ uv run tau2 check-data
DEBUG | tau2.registry:<module>:297 - Voice dependencies not installed, skipping voice user registration
tau2 Data Directory Checker
========================================
Data directory: .../tau2-bench/data
✅ Data directory exists
You can now run tau2 commands.
```

The voice message is expected: this project uses the text retail domain only.

### Model smoke test

One completion through the pinned LiteLLM, with the key loaded from the clone's `.env`:

```text
$ uv run python -c "from dotenv import load_dotenv; load_dotenv(); import litellm; \
    r=litellm.completion(model='gpt-6-luna', messages=[{'role':'user','content':'Reply with OK'}]); \
    print(r.model, r.choices[0].message.content)"
gpt-6-luna OK
```

- LiteLLM 1.81.11 resolves the bare name `gpt-6-luna` to OpenAI; no `openai/` prefix is needed.
- It also printed Pydantic serializer warnings about the response `Message` shape. They did not affect the result.
- **Cost will not be reported for `gpt-6-luna`.** The model is missing from the pinned LiteLLM price map, and τ-bench's `get_response_cost` (`src/tau2/utils/llm_utils.py`) logs the error and returns `0.0`. Run costs for [#41](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/41) must come from token usage and published prices, or the OpenAI usage dashboard, not from τ-bench's cost field.

### Credentials

`.env` exists only in `../tau2-bench`, created from its `.env.example`, with only `OPENAI_API_KEY` set. Upstream's `.gitignore` ignores it (`git check-ignore -v .env` → `.gitignore:153:.env`). This repository contains no `.env` file and none in its history.

### Deviations from the first-run reference commands

Compared with [`DEVELOPMENT.MD` — First-run reference commands](../DEVELOPMENT.MD#first-run-reference-commands):

1. **Pinned commit.** The reference clones `main` without pinning. We record the commit above and check it out explicitly to reproduce.
2. **Python.** No command changed, but the system Python (3.14.3) is out of range. `uv` selected its managed 3.12.0 from upstream's `.python-version`.
3. **Model.** The smoke test uses `gpt-6-luna` instead of the `gpt-4.1` example. The model choice itself is recorded in [#43](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/43).
4. **Scope.** Only `uv sync`, `cp .env.example .env`, and `tau2 check-data` ran in this step. `tau2 intro`, `tau2 run`, and `tau2 view` belong to later tickets.

## Retail domain

Recorded on October 26, 2026 for [#40](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/40) (plan step 2.5). Everything below was read from the pinned checkout (`0c69df4`); links point at that commit, and paths are relative to `../tau2-bench`.

| Source | Path |
| --- | --- |
| Tools | [`src/tau2/domains/retail/tools.py`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/domains/retail/tools.py) |
| Policy (the agent's system-prompt policy) | [`data/tau2/domains/retail/policy.md`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/data/tau2/domains/retail/policy.md) |
| Tasks | [`data/tau2/domains/retail/tasks.json`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/data/tau2/domains/retail/tasks.json) and `split_tasks.json` |
| Database | `data/tau2/domains/retail/db.json` — 50 products, 500 users, 1,000 orders (423 pending, 373 delivered, 102 processed, 102 cancelled) |
| Environment wiring | `src/tau2/domains/retail/environment.py` |

### Tool inventory

The retail toolkit exposes 16 agent tools: 7 `READ`, 2 `GENERIC`, and 7 `WRITE`. Retail has no user-side tools, and `get_environment` rejects solo mode. Each tool's class comes from its `@is_tool(ToolType…)` decorator. Whether a tool **mutates state** follows from that class: `is_tool` sets `mutates_state=True` only for `WRITE` unless the decorator overrides it ([`toolkit.py:82`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/environment/toolkit.py#L82)), and no retail tool overrides it. Only mutating tools are re-executed when the evaluator replays a conversation (see [Scoring](#scoring)).

| Tool | Arguments | Class | Mutates the DB | What it changes, and the checks the tool itself enforces |
| --- | --- | --- | --- | --- |
| `find_user_id_by_email` | `email` | `READ` | No | Case- and diacritic-folded match; errors if not found or ambiguous |
| `find_user_id_by_name_zip` | `first_name`, `last_name`, `zip` | `READ` | No | Same matching rules as above |
| `get_user_details` | `user_id` | `READ` | No | Returns name, email, default address, payment methods, and order IDs |
| `get_order_details` | `order_id` | `READ` | No | Returns status, items, fulfillments, payment history |
| `get_product_details` | `product_id` | `READ` | No | Returns a product and all its variants |
| `get_item_details` | `item_id` | `READ` | No | Returns one variant (item) |
| `list_all_product_types` | — | `READ` | No | Names and product IDs of all 50 product types |
| `calculate` | `expression` | `GENERIC` | No | Arithmetic only; rejects other characters |
| `transfer_to_human_agents` | `summary` | `GENERIC` | No | Returns `"Transfer successful"`; changes nothing |
| `cancel_pending_order` | `order_id`, `reason` | `WRITE` | Yes | Status must be exactly `pending`; reason must be `no longer needed` or `ordered by mistake`. Sets `cancelled`, appends refunds, credits gift cards immediately |
| `modify_pending_order_address` | `order_id`, `address1`, `address2`, `city`, `state`, `country`, `zip` | `WRITE` | Yes | Status must *contain* `pending` (so `pending (item modified)` also passes). Replaces the shipping address |
| `modify_pending_order_payment` | `order_id`, `payment_method_id` | `WRITE` | Yes | Status must contain `pending`; exactly one prior payment; new method must differ and be the user's; gift card must cover the total. Appends payment + refund, adjusts gift-card balances |
| `modify_pending_order_items` | `order_id`, `item_ids`, `new_item_ids`, `payment_method_id` | `WRITE` | Yes | Status must be exactly `pending`; items must be in the order; each new item must be a different, available variant of the same product; gift card must cover the difference. Sets `pending (item modified)` |
| `modify_user_address` | `user_id`, `address1`, `address2`, `city`, `state`, `country`, `zip` | `WRITE` | Yes | Replaces the user's default address |
| `exchange_delivered_order_items` | `order_id`, `item_ids`, `new_item_ids`, `payment_method_id` | `WRITE` | Yes | Status must be `delivered`; items must be in the order; each new item must be an available variant of the same product; gift card must cover the difference. Unlike modify items, it does **not** reject a new item ID equal to the old one. Sets `exchange requested` and records the price difference |
| `return_delivered_order_items` | `order_id`, `item_ids`, `payment_method_id` | `WRITE` | Yes | Status must be `delivered`; refund method must be the original payment method or any gift card of the user. Sets `return requested` |

A `think` tool is present but commented out (`tools.py:761`), so it is not available.

Observations for later tickets:

- **No tool checks identity or confirmation.** Lookups accept any user, order, or product ID; no tool requires a prior authentication call, and writes do not require a confirmation turn. Upstream records this as a known issue on task `5`: "Agent is allowed to do user level actions e.g. look-up their order info, without authenticating the user." Authentication and confirmation are policy rules the model is asked to follow, not constraints the tools enforce.
- **Status checks are looser for some writes.** `modify_pending_order_address` and `modify_pending_order_payment` use `_is_pending_order` (`"pending" in order.status`), so both pass their status check on an order already in `pending (item modified)`. Only the address change actually goes through: item modification appends a payment-history entry, and payment modification then fails its "exactly one payment" check. The policy says such an order can no longer be modified, so changing the address after an item modification is a gap between the tools and the policy.
- **The policy's "different option" rule is enforced for modifications but not exchanges.** `modify_pending_order_items` rejects a new item ID equal to the old one; `exchange_delivered_order_items` has no such check and accepts an identical, available item ID.
- **Possible upstream defect, unverified at runtime.** In `modify_pending_order_items`, the loop that rewrites the order's items assigns `item.price = variant.price` and `item.options = variant.options` (`tools.py:583`) using the `variant` left over from the *previous* loop, so with several items every modified item may get the last new item's price and options. To confirm or rule out in [#47](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/47).
- The code writes `pending (item modified)`; the policy text spells it `pending (items modifed)`.

### Policy summary

Source: `data/tau2/domains/retail/policy.md` at `0c69df4` (136 lines). It is passed to the agent as the domain policy by `get_environment`.

**Scope.** The agent may cancel or modify pending orders, return or exchange delivered orders, change the user's default address, and give information about the user's own profile, orders, and products.

**Conversation rules.**

1. Authenticate first, by finding the user ID through email or through name + zip code, even if the user already gave a user ID.
2. Serve one user per conversation; deny requests about any other user.
3. Before any action that updates the database (cancel, modify, return, exchange), list the action details and get explicit confirmation ("yes").
4. Do not make up information or procedures, and do not give subjective recommendations.
5. Make at most one tool call at a time, and never call a tool and reply to the user in the same turn.
6. Deny requests that go against the policy.
7. Transfer to a human if and only if the request is out of scope: call `transfer_to_human_agents`, then send exactly `YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.`

**Action rules.**

| Action | Allowed when | Key rules |
| --- | --- | --- |
| Cancel | Status is `pending` (check first) | User confirms order ID and reason; only `no longer needed` or `ordered by mistake`. Refund goes to the original method: immediately for gift cards, otherwise 5–7 business days |
| Modify address | Status is `pending` | Only address, payment, or item options can change on a pending order |
| Modify payment | Status is `pending` | One payment method, different from the original; a gift card must cover the total |
| Modify items | Status is `pending` | Callable once per order; afterwards the order can't be modified or cancelled. Remind the user to list every item first. Same product, different option, available; a payment method covers the difference |
| Return | Status is `delivered` (check first) | User confirms order ID and items; refund to the original method or an existing gift card. Status becomes `return requested` |
| Exchange | Status is `delivered` (check first) | Remind the user to list every item; same product, different option, available; payment method covers the difference. Callable once per order. Status becomes `exchange requested`; no new order |

Generally, actions apply only to pending or delivered orders. Times in the database are EST, 24-hour.

### Task schema

Each entry in `tasks.json` validates against the `Task` model in [`src/tau2/data_model/tasks.py`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/data_model/tasks.py). There are 114 retail tasks with IDs `"0"`–`"113"`; `split_tasks.json` defines `base` (all 114), `train` (74), and `test` (40). `get_tasks()` defaults to `base`.

| Field | Content in the retail tasks |
| --- | --- |
| `id` | String task ID, `"0"`–`"113"` |
| `description` | `purpose`, `relevant_policies`, `notes` — non-null in only 3 tasks |
| `user_scenario.persona` | `null` in all retail tasks |
| `user_scenario.instructions` | Structured instructions for the simulated user: `domain` (`retail`), `reason_for_call`, `known_info` (for example name and zip), `unknown_info` (for example "You do not remember your email address"), `task_instructions` (behavior) |
| `initial_state` | `null` in all retail tasks; every run starts from `db.json` |
| `evaluation_criteria.actions` | One reference trajectory: `action_id`, `requestor` (default `assistant`), `name`, `arguments`, `info`, optional `compare_args` |
| `evaluation_criteria.communicate_info` | Strings the agent should say; non-empty in 36 tasks |
| `evaluation_criteria.nl_assertions` | Natural-language expectations for an LLM judge; non-empty in 40 tasks |
| `evaluation_criteria.env_assertions` | Not present in any retail task |
| `evaluation_criteria.reward_basis` | `["DB", "NL_ASSERTION"]` in 112 tasks; `["DB"]` in tasks `33` and `34` |
| `issues` | Upstream review notes; present on tasks `4`, `5`, and `7` |

Across the 114 tasks, the reference trajectories contain 550 actions. 10 tasks (`10`, `12`, `24`, `25`, `50`, `57`, `62`, `65`, `67`, `68`) contain no write, so their target end state is the unchanged database. Four actions are `transfer_to_human_agents`.

**English replacement tasks.** The English language pack (`data/tau2/multilingual/en/retail_tasks_en.json`) holds same-ID replacements for 50 tasks. Compared field by field with `tasks.json`, they differ only in `user_scenario`: `known_info` gains a "Name details" block giving first- and last-name order. Their `evaluation_criteria` are identical, so scoring is unaffected. Upstream's CHANGELOG says every run resolves the English pack by default, while `docs/language-packs.md` says custom text runs without `--language` use the ordinary tasks; [#41](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/41) records which one the baseline run actually used.

## Scoring

Recorded on October 26, 2026 for [#40](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/40). Sources: upstream's [evaluation documentation](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/docs/evaluation.md) and the evaluator code in [`src/tau2/evaluator/`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/), both at `0c69df4`. Where they disagree, the code wins.

### How reward is computed

1. **Termination gate.** A conversation that did not end by `AGENT_STOP` or `USER_STOP` (for example, it hit the turn limit or an error) scores `0.0`, and no other check runs ([`evaluator.py:123`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/evaluator.py#L123)).
2. **Components.** The final reward is the **product** of the components listed in the task's `reward_basis`. Each component is 0 or 1, so reward is binary in practice. The run path defaults to `EvaluationType.ALL` (`src/tau2/run.py:85`), which applies the basis ([`evaluator.py:202`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/evaluator.py#L202)).
3. **DB state (`DB`).** `EnvironmentEvaluator` ([`evaluator_env.py`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/evaluator_env.py)) builds two environments from `db.json`:
   - *predicted*: replays the agent's actual tool calls from the conversation; only mutating (`WRITE`) tools are re-executed;
   - *gold*: replays the task's `evaluation_criteria.actions`. A failing gold action is logged and skipped.

   It then compares SHA-256 hashes of the two databases. A match gives `1.0`, anything else `0.0`. There is no partial credit and no per-field diff in the reward.
4. **Natural-language assertions (`NL_ASSERTION`).** `NLAssertionsEvaluator` sends the transcript and the task's `nl_assertions` to an LLM judge, `gpt-5.4-mini-2026-03-17` at `reasoning_effort: low` (`src/tau2/config.py:45`). The component is `1.0` only if the judge marks every assertion met. With no assertions it is `1.0` without calling the judge ([`evaluator_nl_assertions.py:34`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/evaluator_nl_assertions.py#L34)). Upstream labels this component experimental.
5. **Communicated info (`COMMUNICATE`).** `CommunicateEvaluator` checks that each `communicate_info` item was said, by a semantic judge (`gpt-5.5`, low effort) or, with `--communicate-scorer substring`, by substring match. Under `ALL` it runs only when `COMMUNICATE` is in the basis ([`evaluator.py:217`](https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/evaluator/evaluator.py#L217)).
6. **Action matching (`ACTION`).** `ActionEvaluator` checks that each reference action has a matching agent tool call (same name; arguments compared per `compare_args`, or all of them). It always runs and is reported (`action_checks`, `partial_action_reward`), but gates reward only when `ACTION` is in the basis.

**What this means for retail at `0c69df4`.**

| Component | Gates retail reward? | Why |
| --- | --- | --- |
| DB state | Yes, all 114 tasks | `DB` is in every retail `reward_basis` |
| NL assertions | Yes, in 40 tasks | `NL_ASSERTION` is in 112 bases, but only 40 tasks have non-empty `nl_assertions`; the rest score `1.0` automatically |
| Communicated info | **No** | No retail task lists `COMMUNICATE`, so the 36 non-empty `communicate_info` lists are not checked under `ALL` |
| Action matching | **No** | No retail task lists `ACTION`; reported as a diagnostic only |
| Env assertions | No | No retail task defines any |

So a retail task scores `1.0` when the conversation ends normally, the final database equals the reference end state, and (in 40 tasks) an LLM judge accepts the assertions. Note that upstream's documentation describes the default basis as `["DB", "COMMUNICATE"]`. That is the schema default, but every retail task sets its basis explicitly, so the default never applies here.

Upstream's worked example (airline task `1`) shows the consequence: when every reference action is read-only, the target state is the initial database, and an agent that only refuses, with no lookups at all, gets full reward. In retail this holds only where no NL assertion also gates reward. Of the 10 retail tasks with read-only reference actions, 6 (`10`, `12`, `25`, `50`, `57`, `65`) have no NL assertions; the other 4 (`24`, `62`, `67`, `68`) also require the judge to confirm that the agent told the user specific facts. For example, task `68` requires "Agent should tell the user the order total is $829.43.", which a bare refusal fails.

### What τ-bench success does not guarantee

This answers [milestone criterion 3](../DEVELOPMENT.MD#milestone-2--install-and-run-τ-bench): a reward of `1.0` is evidence about the **end state**, not about the **process**. It does not show that:

- **The user was authenticated.** No tool requires authentication, and no reward component checks it (upstream issue on task `5`). Upstream's authentication classifier (`auth_classifier.py`) is part of the optional reviewer, not the reward.
- **Confirmation was obtained before a write.** The DB hash is the same whether or not the agent asked "yes?" first. Only an NL assertion could catch a missing confirmation, and none of the retail NL assertions checks confirmation or authentication. In 33 tasks they check facts told to the user (for example, "Agent should tell the user the total refund amount is $918.43."). In the other 7 (`105`, `106`, `107`, `109`, `110`, `111`, `113`) they check that an action happened (for example, "Agent should process the exchange.").
- **Only the right user's data was touched or disclosed.** Reads leave no trace in the DB, so looking up or revealing another user's orders does not lower the DB component, and no retail NL assertion checks for it.
- **The policy's conversation rules were followed.** One tool call at a time, no tool call and reply in the same turn, no invented information, and the exact transfer message are not checked by any gating component.
- **The right path was taken.** Any sequence of calls that reaches the same end state passes the DB component. A task passes outright when the remaining components also pass: for example, a refusal with zero tool calls passes a read-only task without NL assertions, such as task `10`. `partial_action_reward` measures similarity to one reference path, not correctness.
- **The user was told what they needed.** `communicate_info` does not gate retail reward. Required facts are checked only where an NL assertion covers them (33 tasks; the other 7 tasks with NL assertions check action outcomes instead), and then by an LLM judge whose verdicts are not deterministic.
- **Results are stable.** One trial per task, an LLM-simulated user, and an LLM judge mean the same task can score differently across runs. Upstream's `pass^k` metric exists for this reason.
- **Intermediate states were valid.** Only the final DB is compared, so a wrong write that a later write fully reverses (for example, an address changed and changed back) still passes the DB component.

For this lab, that means the independent process and policy checks in [`EVALUATION.md`](EVALUATION.md) (deterministic checks, judge rubrics, and the authorization gate's own logs) are required alongside τ-bench reward, not optional. [#48](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/48) classifies which of these checks can be deterministic.
