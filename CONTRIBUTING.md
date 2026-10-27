# Contributing

This repository demonstrates how a production AI application is planned, built, observed, and governed. The process is part of the demonstration, so every change follows it, including changes by the sole maintainer.

## Workflow at a glance

```
issue → branch → commits → pull request → review → merge (squash or rebase) → issue closed
```

Work follows a **design → implement → verify → review** rhythm:

1. **Design** — the issue states the objective and its Acceptance Criteria before work starts.
2. **Implement** — make the change on a branch.
3. **Verify** — demonstrate each criterion with real evidence (commands, tests, run manifests, trace IDs). Never invent results.
4. **Review** — open a pull request, address the Copilot review, check the definition of done, then merge.

## Issues

File every piece of work as an issue using one of the templates (blank issues are disabled):

| Template | Use for | Labels applied automatically |
| --- | --- | --- |
| **Milestone task** | Planned work from `DEVELOPMENT.MD` | none — add them yourself (see below) |
| **Decision / ADR** | Proposing or recording a decision for `docs/DECISIONS.md` | `type:decision`, `area:governance` |
| **Bug** | Behavior that does not match a spec | `type:bug` |

Every issue has an **Acceptance Criteria** section of checkable items. An issue is closed only when all of them are met.

Assign each issue to its milestone. Milestones mirror the milestones in [`DEVELOPMENT.MD`](DEVELOPMENT.MD); each milestone's description holds its objective and Acceptance Criteria.

When planning a milestone's tickets, cover every open question in [`docs/DECISIONS.md`](docs/DECISIONS.md#open-questions-to-resolve-deliberately) that targets that milestone, with its own ticket or an Acceptance Criteria item on an existing one. Before the milestone closes, each such question is resolved (decision recorded) or re-targeted with a stated reason; see the [working definition of a milestone](DEVELOPMENT.MD#working-definition-of-a-milestone).

## Milestone status

`DEVELOPMENT.MD` is the progress tracker, so keep it current while a milestone is under way, not only at sign-off:

- **Milestone start:** the first pull request of a milestone sets the milestone's row in the `DEVELOPMENT.MD` milestone table to **In progress** and updates the [current execution checklist](DEVELOPMENT.MD#4-current-execution-checklist) and the status lines at the top of `DEVELOPMENT.MD` and `README.md`.
- **Plan steps:** each issue's **Plan step** field names the `DEVELOPMENT.MD` steps it serves. A pull request that closes the last open issue for a step ticks that step in the same pull request. An issue whose plan step is a process step ticks nothing.
- **Milestone end:** marking the milestone **Complete** and ticking its Acceptance Criteria belong to the milestone's sign-off issue.

## Labels

The canonical label set lives in [`.github/labels.yml`](.github/labels.yml). Labels on GitHub must match that file; change both together.

| Group | Labels | Rule |
| --- | --- | --- |
| Type | `type:docs`, `type:process`, `type:decision`, `type:review`, `type:bug` | At least one per issue |
| Area | `area:scope`, `area:evaluation`, `area:autonomy`, `area:architecture`, `area:simulation`, `area:operations`, `area:demo`, `area:governance` | One or more for project work |
| Status | `needs-review`, `blocked` | Add or remove as the state changes |
| Other | `hygiene` | Repository upkeep: ignore rules, dependencies, stale docs, broken links |

## Architecture

Lab code follows ports-and-adapters ([ADR-013](docs/DECISIONS.md#adr-013--adopt-the-ports-and-adapters-architecture); ports and adapters are listed in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#code-structure-ports-and-adapters)).

- **Dependency rule:** code in `domain/`, `ports/`, and `application/` never imports τ-bench (`tau2`), LangSmith (`langsmith`), or a provider SDK (`openai`, `anthropic`, `litellm`). Only `adapters/` and `entrypoints/` do.
- New infrastructure goes behind an existing port as a new adapter. Add or change a port only when the core needs a capability no port offers, and add an in-memory test double with it.
- Enforcement: from Milestone 3, `import-linter` contracts in `pyproject.toml` check the rule; run `lint-imports` before opening a pull request. Until code exists, reviewers check it by hand.

## Branches

Branch from `main` and name branches `m<milestone>/<issue>-<short-slug>`, for example `m1/12-evaluation-metrics`.

A branch may cover a whole milestone when its tickets are small and sequential (Milestone 0 used `m0/repository-setup`). In that case, use one commit per issue.

## Commits

- Subject line: imperative mood, capitalized, no trailing period, about 50–72 characters (`Add pull request template ...`).
- Body: what changed and why, wrapped at about 72 characters.
- Reference the issue in the body: `Closes #N` when the commit completes it, `Refs #N` otherwise.

## Pull requests

- Use the [pull request template](.github/pull_request_template.md): link the issue (`Closes #N`), summarize the change, describe verification, and complete the definition-of-done checklist.
- **GitHub Copilot reviews every pull request.** A review is requested automatically when a non-draft pull request is opened and again on each new push. Open as a draft to iterate without triggering reviews.
- Address every Copilot comment: either fix it, or reply with the reason it does not apply. Then resolve the conversation. All conversations must be resolved before merging.
- Merge with one of the two enabled methods. Merge commits are disabled, and both methods keep history linear:
  - **Squash and merge** (default): the pull request lands as one commit. Use it when the individual commits are work-in-progress.
  - **Rebase and merge**: each commit lands on `main` as a separate commit (rebased, so commit IDs change). Use it when the individual commits are meaningful history, such as one commit per ticket or review-fix commits worth keeping (as in #23).
- The source branch is deleted automatically after merge.

## Definition of done

A change is done when:

- every Acceptance Criteria item in the linked issue is met and verified;
- `DEVELOPMENT.MD` reflects any change to scope, order, or acceptance criteria;
- `DEVELOPMENT.MD` milestone status and plan steps are current (see [Milestone status](#milestone-status));
- any decision is recorded in `docs/DECISIONS.md`;
- code follows the [dependency rule](#architecture): no τ-bench, LangSmith, or provider SDK imports outside adapters and the composition root;
- no secrets, credentials, or real PII are committed (all customer data is synthetic);
- any reported result comes from a recorded run or is clearly labeled illustrative.

## Protection of `main`

A repository ruleset named **Protect main** applies to the default branch:

- changes must arrive through a pull request;
- linear history is required;
- force-pushes and branch deletion are blocked;
- review conversations must be resolved before merge;
- GitHub Copilot review is requested automatically on every pull request.

**Single-maintainer exception:** the project has one maintainer, who acts as stakeholder, product manager, engineer, and SDET, and GitHub does not allow approving your own pull request. **GitHub Copilot is the reviewer.** Copilot leaves comment reviews and cannot approve, so required approvals are set to **0**. The review still has to happen: every Copilot comment must be addressed and resolved, and the maintainer accepts the change by merging. Raise required approvals to 1 when a second maintainer joins.

## Secrets and data

Never commit `.env` files, API keys, or real customer data. Put placeholders in `.env.example`. Raw simulation results and trace exports are ignored by `.gitignore`; commit curated manifests and summaries instead.
