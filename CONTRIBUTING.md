# Contributing

This repository demonstrates how a production AI application is planned, built, observed, and governed. The process is part of the demonstration, so every change follows it, including changes by the sole maintainer.

## Workflow at a glance

```
issue → branch → commits → pull request → review → squash-merge → issue closed
```

Work follows a **design → implement → verify → review** rhythm:

1. **Design** — the issue states the objective and its Acceptance Criteria before work starts.
2. **Implement** — make the change on a branch.
3. **Verify** — demonstrate each criterion with real evidence (commands, tests, run manifests, trace IDs). Never invent results.
4. **Review** — open a pull request, check the definition of done, then merge.

## Issues

File every piece of work as an issue using one of the templates (blank issues are disabled):

| Template | Use for | Labels applied automatically |
| --- | --- | --- |
| **Milestone task** | Planned work from `DEVELOPMENT.MD` | none — add them yourself (see below) |
| **Decision / ADR** | Proposing or recording a decision for `docs/DECISIONS.md` | `type:decision`, `area:governance` |
| **Bug** | Behavior that does not match a spec | `type:bug` |

Every issue has an **Acceptance Criteria** section of checkable items. An issue is closed only when all of them are met.

Assign each issue to its milestone. Milestones mirror the milestones in [`DEVELOPMENT.MD`](DEVELOPMENT.MD); each milestone's description holds its objective and Acceptance Criteria.

## Labels

The canonical label set lives in [`.github/labels.yml`](.github/labels.yml). Labels on GitHub must match that file; change both together.

| Group | Labels | Rule |
| --- | --- | --- |
| Type | `type:docs`, `type:process`, `type:decision`, `type:review`, `type:bug` | At least one per issue |
| Area | `area:scope`, `area:evaluation`, `area:autonomy`, `area:architecture`, `area:simulation`, `area:operations`, `area:demo`, `area:governance` | One or more for project work |
| Status | `needs-review`, `blocked` | Add or remove as the state changes |
| Other | `hygiene` | Repository upkeep: ignore rules, dependencies, stale docs, broken links |

## Branches

Branch from `main` and name branches `m<milestone>/<issue>-<short-slug>`, for example `m1/12-evaluation-metrics`.

A branch may cover a whole milestone when its tickets are small and sequential (Milestone 0 used `m0/repository-setup`). In that case, use one commit per issue.

## Commits

- Subject line: imperative mood, capitalized, no trailing period, about 50–72 characters (`Add pull request template ...`).
- Body: what changed and why, wrapped at about 72 characters.
- Reference the issue in the body: `Closes #N` when the commit completes it, `Refs #N` otherwise.

## Pull requests

- Use the [pull request template](.github/pull_request_template.md): link the issue (`Closes #N`), summarize the change, describe verification, and complete the definition-of-done checklist.
- Resolve all review conversations before merging.
- Merge with **squash-merge** (the only enabled merge method). The source branch is deleted automatically.

## Definition of done

A change is done when:

- every Acceptance Criteria item in the linked issue is met and verified;
- `DEVELOPMENT.MD` reflects any change to scope, order, or acceptance criteria;
- any decision is recorded in `docs/DECISIONS.md`;
- no secrets, credentials, or real PII are committed (all customer data is synthetic);
- any reported result comes from a recorded run or is clearly labeled illustrative.

## Protection of `main`

A repository ruleset named **Protect main** applies to the default branch:

- changes must arrive through a pull request;
- linear history is required;
- force-pushes and branch deletion are blocked;
- review conversations must be resolved before merge.

**Single-maintainer exception:** required approvals are set to **0** because the project has one maintainer, who cannot approve their own pull requests. Reviews still happen through the pull request checklist. Raise this to 1 when a second maintainer joins.

## Secrets and data

Never commit `.env` files, API keys, or real customer data. Put placeholders in `.env.example`. Raw simulation results and trace exports are ignored by `.gitignore`; commit curated manifests and summaries instead.
