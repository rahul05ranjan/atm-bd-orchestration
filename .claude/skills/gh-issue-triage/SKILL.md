---
name: gh-issue-triage
description: Triage a repository's open GitHub issues in waves of increasing model class (haiku, sonnet, opus), classify type and P1-P4 priority, schedule issues to releases, and close issues only on verified QA-approved and released evidence. Use for backlog triage, release planning, and closing out fixed issues.
---

# GitHub issue triage

Categories, the priority test, the lifecycle labels and the exact evidence
comments are in [references/issue-classification.md](references/issue-classification.md).
Read it before any wave; this file is only the procedure.

## Principles

- Labels are the state, comments are the evidence. A release label means
  "expected in that release". `qa-approved` + `QA-APPROVED:` comment means a
  QA-passed fix landed at a PR and commit. `fixed-released` + `FIXED-RELEASED:`
  comment means that commit is in a published release. Close needs both.
- Cheap models do mechanical work (read a comment, run an ancestry check);
  stronger models make judgements. Each wave consumes the previous wave's
  output and may overrule it, and says so when it does.
- Triage agents never close an issue on their own reading of code. The only
  close path is the evidence in the two comments.
- Read-only on code (check out the release tag or use `git show <tag>:<path>`);
  write only labels and the two comment forms. REST only (`gh api`); never
  `gh pr list` or `gh pr checks`; no per-issue GraphQL fan-out.

## Setup (once per repository)

Ensure the labels exist: `P1 P2 P3 P4 bug "feature request" qa-approved
fixed-released` and one label per release (`0.18.4` style). Label
descriptions are at most 100 characters. List the open issues with one call:

```bash
gh api 'repos/<o>/<r>/issues?state=open&per_page=100' --paginate \
  --jq '.[]|select(.pull_request|not)|[.number,([.labels[].name]|join(","))]|@tsv'
```

Pin the reference release (the newest tag) and its checkout path. Give every
agent that path, the tag, and the rule that it is read-only.

## Waves

Run waves as parallel agents, batches of about five issues each so a failed
agent costs little. Each agent returns JSON per issue (number, old labels,
new labels, one-line deciding reason); the lead re-reads the live labels
after every wave instead of trusting the report.

### Wave 1: haiku, mechanical

1. **Close-out check.** Issues with `qa-approved`: run the four mechanical
   checks in the reference (QA comment `items=all`, PR merged, commit in the
   release tag, release published). All hold: add `fixed-released`, post the
   FIXED-RELEASED line, close. Any fails: change nothing and report which.
2. **First classification.** New issues (no type or P): split into numbered
   items, set one type and one P by the reference, add nothing else. A haiku
   pass is fast and rough; expect it to over-rank. That is acceptable only
   because wave 2 follows.

### Wave 2: sonnet, judgement

1. **Re-rank by evidence.** Re-judge every issue at P1 and P2 from the issue
   text, comments and the code at the release; demote or promote with a
   deciding fact, not inertia. Apply the distribution guard.
2. **Re-type doubtful issues.** Any issue whose type looks wrong (a "bug"
   that is a design or policy request, a feature that is a real defect).
3. **Backlog completion sweep.** For old issues that may already be fixed:
   split into items, cite `file:line` plus a test per item at the release.
   All items cited: mark `fixed-released` and comment with the evidence; do
   not close without a QA record (the lead closes).

### Wave 3: opus, calibration and scheduling

Reads every open issue's classification, not just a batch, and:

1. Fixes the distribution: if a tier holds more than half of the issues,
   splits it using the priority test, never by quota alone.
2. Resolves disagreements between waves and between related issues
   (duplicates and issues that share a root cause get one priority and a
   cross-reference comment).
3. Assigns release labels: every P1 to the next release, P2 to the next or
   following release by capacity, nothing else without a reason.
4. Applies the slip rule for the release that just shipped.
5. Reports the final table (number, type, P, release, state) and anything it
   left alone.

### Lead

The lead posts the `QA-APPROVED:` comment and the `qa-approved` label when a
QA-passed fix lands, and decides what the release contains. After every wave
the lead re-reads the label distribution and spot-checks a few comments
against the code. A wave that did not read its evidence is re-run, not
patched.

## Reporting

State per wave: issues in, labels changed, issues closed, distribution
before and after, and what was not verified. Truthful nulls ("no issue was
fully done") are valid results.

## Lessons recorded from the first run

- Haiku put 27 of 40 issues at P2 and none at P1; sonnet then put 34 of 40
  at P3. Neither tier discriminated. Wave 3 and the distribution guard exist
  for that.
- Issues are bundles; the verifying agents were right to report PARTIAL for
  five of five issues that looked done. Never close on a CHANGELOG line.
- Forgetting the type split (`bug` versus `feature request`) mislabelled
  features as bugs; check type before priority.
