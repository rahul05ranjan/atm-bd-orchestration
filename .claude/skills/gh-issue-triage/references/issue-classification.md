# Issue classification

Every open issue carries one **type**, one **priority**, and sits in one
**lifecycle state**. Labels are the state; comments carry the evidence.

## Type (exactly one)

| Label | Meaning |
|---|---|
| `bug` | Shipped behaviour is wrong, or shipped documentation tells the reader to do the wrong thing. |
| `feature request` | A new capability, a design or process change, or a policy decision. Nothing shipped is broken. |

A mis-sized or "needs more design" issue is a `feature request`. An issue
whose text says "refuse X" where the code refuses X by design is a
`feature request`, not a `bug`. Remove stale `enhancement` when
`feature request` is added.

## Priority (exactly one)

Judge by failure risk **in the shipped release**, never by age, effort,
comment count or how easy the fix is.

| Label | Test | Typical |
|---|---|---|
| `P1` | Any one of Rand's three triggers (2026-10-21), or a phase that cannot run, a silently wrong result, or data loss with no practical workaround. **(a)** The system sends an erroneous message, or spams ATM with redundant or misleading information. **(b)** A Jev call returns an improper error, or Jev is used misaligned with its contract: Jev is a critical failure point in the skill. **(c)** An error makes the TUI show improper information (wrong, stale-as-fresh, blank or misleading content). | Bare or repeated alert per poll; false CI-failed alert; Jev evidence error on a null PR body; console header shows the wrong phase. |
| `P2` | A core flow (dispatch, dev-sanity, QA, stack handoff, release) or console correctness on live data hurts **regularly** and the workaround is expensive, error-prone or needs a person each time. | Quota burned every poll; every large layer needs a lead ruling. |
| `P3` | Occasional, or a cheap known workaround exists, or the effect is noise or inefficiency; a doc that still tells the reader to do the wrong thing. | Bare alert without a bead id; a template step that wastes a call. |
| `P4` | Cosmetic, a doc that is stale but not misleading, or an idea with nothing observed broken. | Icon colours; Azure DevOps support; install profiles. |

**Distribution guard.** P1 is defined by its triggers, not a quota: every issue that
meets a trigger is P1, so the P1 share follows the backlog. For the other tiers aim for roughly P2 20-30%,
P3 35-45%, P4 20-30% of the remainder. A wave reports its distribution. If one
tier holds more than half the issues, the next wave must re-split that tier
(a tier that does not discriminate has failed its job). The first run of this
process went 27 of 40 at P2, then 34 of 40 at P3.

## Lifecycle (labels are the state)

```
new (no type/P)
  → triaged         type + P
  → scheduled       release label X.Y.Z (0.18.4 style, already exists in the repo)
  → qa-approved     label `qa-approved` + QA-APPROVED comment (PR + commit)
  → fixed-released  label `fixed-released` + FIXED-RELEASED comment (release + commit)
  → closed
```

- **`scheduled`**: an issue carrying a release label is expected to be
  completed in that release. Only the lead/triage-opus wave adds one.
  Priority orders the work inside a release; a P1 always carries the next
  release label.
- **Slip rule**: when a release ships, an open issue still carrying its label
  without `fixed-released` has slipped. Re-label to the next release or
  demote its priority; never leave a shipped release label on an open issue.
- **`qa-approved`**: added by the lead (or quality-mgr) when a QA PASS fix
  has landed. It is never set by triage agents from reading code.
- **`fixed-released`**: added by a verifying wave once the landed commit is
  in a published release.
- **Close** only when both comments below verify. A fix that landed but was
  not QA-approved is not closed; it is reported.

## Evidence comments (one line, machine-checkable)

QA approval, posted by the lead/quality-mgr at landing:

```
QA-APPROVED: pr=#<n> commit=<full-sha> qa=<task id or report link> items=<all|list of remaining item numbers>
```

Release, posted by a verifying wave (never copied from a CHANGELOG):

```
FIXED-RELEASED: release=v<x.y.z> commit=<full-sha> verified=<date> by=<model>
```

Verification, mechanical:

1. `QA-APPROVED` present, `items=all` (otherwise the issue is partly done and
   stays open with the remaining items named in a comment).
2. The PR is merged: `gh api repos/<o>/<r>/pulls/<n> --jq .merged_at` and the
   commit is reachable from the base.
3. The release contains it: `git merge-base --is-ancestor <sha> v<x.y.z>`
   (or `gh api repos/<o>/<r>/compare/v<x.y.z>...<sha> --jq .status` is
   `behind` or `identical`).
4. The release exists and is published (tag, GitHub release, package index
   as the repo publishes).

All four hold: add `fixed-released`, post the FIXED-RELEASED line, close.
Any fails: change nothing, report which check failed.

## Done-ness evidence when no QA comment exists (backlog sweep)

Old issues were fixed before this process existed. A sweep wave may mark them
`fixed-released` only with all of: each numbered item of the issue cited at
`file:line` in the release, a named test that exercises it, and a QA record or
merged PR that names the issue. A CHANGELOG line alone is never evidence.
Without a QA record the issue gets `fixed-released` but is closed only by the
lead.

## Pitfalls

- An issue is a bundle. Split it into numbered items; "done" means every item.
- A later comment or a NOTE can supersede part of the body; follow it.
- Do not keep a P tier out of inertia; re-judge from the text and the code.
- Never remove version labels (`0.18.x`), they are the schedule.
- Labels with spaces are URL-encoded in the delete path: `feature%20request`.
