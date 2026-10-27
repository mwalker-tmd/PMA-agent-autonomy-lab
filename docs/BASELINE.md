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

Compared with [`DEVELOPMENT.MD` — First-run reference commands](../DEVELOPMENT.MD#first-run-reference-commands-not-yet-executed):

1. **Pinned commit.** The reference clones `main` without pinning. We record the commit above and check it out explicitly to reproduce.
2. **Python.** No command changed, but the system Python (3.14.3) is out of range. `uv` selected its managed 3.12.0 from upstream's `.python-version`.
3. **Model.** The smoke test uses `gpt-6-luna` instead of the `gpt-4.1` example. The model choice itself is recorded in [#43](https://github.com/mwalker-tmd/PMA-agent-autonomy-lab/issues/43).
4. **Scope.** Only `uv sync`, `cp .env.example .env`, and `tau2 check-data` ran in this step. `tau2 intro`, `tau2 run`, and `tau2 view` belong to later tickets.
