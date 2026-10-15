from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from atm_bd_console.dispatch import candidates, dispatch
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.types import (
    DispatchContext,
    DispatchSpec,
    Merge,
    Phase,
    PullRequest,
    RemoteRef,
    RepoConfig,
    Sprint,
    Step,
    Task,
    Worktree,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
REPO = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", "/policy", (), "/plans")
SPRINT = Sprint("atm-bh-6", "tick rules", 6, "sprint/bh-6", "sprint/bh-1", "phase-bh", (), False, 0)


def step(**changes) -> Step:
    value = Step(
        bead_id="atm-bh-6.group-qa", sprint="atm-bh-6", kind="qa", quick_fix=False, anchor=False,
        status="open", ready=True, assignee="solar", branch="sprint/bh-6", target="sprint/bh-1",
        worktree="/worktrees/bh-6", layer=6, difficulty="hard", tag="bh-6", top_epic="atm-phase-bh",
        claimed_at=None, closed_at=None, title="QA", checked_bead="atm-bh-6.group-dev",
        sprint_bead="atm-bh-6", qa_round=1, carry_forward=None,
    )
    return replace(value, **changes)


def phase(*steps: Step) -> Phase:
    return Phase("atm-phase-bh", "phase-bh", "Phase BH", False, (SPRINT,), tuple(steps), ())


def run(value: Step, *, remotes=None, worktrees=None, merges=(), prs=(), spec=None):
    remote_values = (RemoteRef("sprint/bh-6", "abc123"),) if remotes is None else remotes
    worktree_values = (Worktree("/worktrees/bh-6", "sprint/bh-6", "abc123"),) if worktrees is None else worktrees
    renderer = spec or (lambda context: Ok(DispatchSpec("qa", {"checked_bead": context.step.checked_bead})))
    return dispatch(phase(value), (), worktree_values, remote_values, merges, prs, REPO, "fenix", NOW, renderer)


def test_candidate_filter_excludes_anchor_quick_fix_and_open_task() -> None:
    values = (
        step(),
        step(bead_id="anchor", anchor=True),
        step(bead_id="quick", quick_fix=True),
        step(bead_id="closed", status="closed"),
        step(bead_id="tasked"),
    )
    task = Task("tasked", "quality-mgr", "assigned", 1, NOW, NOW, "queued")
    assert tuple(item.bead_id for item in candidates(phase(*values), (task,))) == ("atm-bh-6.group-qa",)


def test_each_hold_is_flat_and_does_not_assign() -> None:
    conflict = Merge("sprint/bh-6", "sprint/bh-1", "conflict", ("model.py",), (), NOW)
    cases = (
        (run(step(ready=False)), "not ready"),
        (run(step(), remotes=()), "branch not on origin"),
        (run(step(), worktrees=()), "no worktree"),
        (run(step(), merges=(conflict,)), "merge conflict with sprint/bh-1: model.py"),
    )
    for outcome, reason in cases:
        assert outcome.actions == ()
        assert len(outcome.held) == 1
        assert reason in outcome.held[0].reason


def test_pr_ci_never_hold_and_dispatch_context_uses_contract_sources() -> None:
    pr = PullRequest(132, "https://example/pr/132", "sprint/bh-6", "sprint/bh-1", "abc123", "failure", ("test",))
    seen: list[DispatchContext] = []

    def render(context: DispatchContext):
        seen.append(context)
        return Ok(DispatchSpec("qa", {"commit": context.commit}))

    outcome = run(step(), prs=(pr,), spec=render)
    assert outcome.held == ()
    assert outcome.actions[0].to == "quality-mgr"
    assert outcome.actions[0].spec == DispatchSpec("qa", {"commit": "abc123"})
    assert seen[0] == DispatchContext(
        step(), SPRINT, "atm-phase-bh", "Phase BH", "phase-bh", "fenix", "abc123",
        "sprint/bh-1", "/worktrees/bh-6", pr, (pr,), REPO,
    )
    sanity = run(step(kind="sanity", sprint="bh-6", sprint_bead=None))
    assert sanity.actions[0].to == "dev-sanity"


def test_spec_error_is_reported_without_assignment() -> None:
    error = NotFound("not_found", "checked_bead", "fix the bead")
    outcome = run(step(), spec=lambda context: Err(error))
    assert outcome.actions == ()
    assert outcome.errors == (error,)


def test_missing_sprint_is_an_error_not_a_silent_drop() -> None:
    outcome = run(step(sprint="missing", sprint_bead="missing"))
    assert outcome.actions == ()
    assert isinstance(outcome.errors[0], NotFound)
    assert "sprint" in outcome.errors[0].what
