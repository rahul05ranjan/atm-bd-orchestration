from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime

from contract_samples import PHASE_TEMPLATES

from atm_bd_console.atm_bd import dispatch_spec
from atm_bd_console.dispatch import candidates, dispatch
from atm_bd_console.enums import CiState, MergeState, PrState, SprintState
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
        status=SprintState.OPEN, ready=True, assignee="solar", branch="sprint/bh-6", target="sprint/bh-1",
        worktree="/worktrees/bh-6", layer=6, difficulty="hard", tag="bh-6", top_epic="atm-phase-bh",
        claimed_at=None, closed_at=None, title="QA", checked_bead="atm-bh-6.group-dev",
        sprint_bead="atm-bh-6", qa_round=1, carry_forward=None,
    )
    return replace(value, **changes)


def phase(*steps: Step) -> Phase:
    return Phase("atm-phase-bh", "phase-bh", "Phase BH", False, (SPRINT,), tuple(steps), ())


def run(value: Step, *, remotes=None, worktrees=None, merges=None, prs=(), spec=None):
    remote_values = (RemoteRef("sprint/bh-6", "abc123"),) if remotes is None else remotes
    worktree_values = (Worktree("/worktrees/bh-6", "sprint/bh-6", "abc123"),) if worktrees is None else worktrees
    renderer = spec or (lambda context: Ok(DispatchSpec("qa", {"checked_bead": context.step.checked_bead})))
    if merges is None:
        merges = (Merge("sprint/bh-6", "sprint/bh-1", MergeState.CLEAN, (), (), NOW),)
    return dispatch(phase(value), (), worktree_values, remote_values, merges, prs, REPO, "fenix", NOW, renderer)


def test_unavailable_merge_holds_until_current_pair_is_verified() -> None:
    unavailable = Merge("sprint/bh-6", "sprint/bh-1", MergeState.ERROR, (), ("timeout; retry the merge check",), NOW)
    wrong_target = replace(unavailable, target="other", state=MergeState.CLEAN)
    for merges in ((), (unavailable,), (wrong_target,)):
        outcome = run(step(), merges=merges)
        assert outcome.actions == ()
        assert "merge verification unavailable" in outcome.held[0].reason
    assert run(step()).actions[0].kind == "assign"


def test_missing_target_holds_with_actionable_reason() -> None:
    value = replace(phase(step(target=None)), sprints=(replace(SPRINT, target=None),))
    outcome = dispatch(value, (), (Worktree("/worktrees/bh-6", "sprint/bh-6", "abc123"),),
                       (RemoteRef("sprint/bh-6", "abc123"),), (), (), REPO, "fenix", NOW, dispatch_spec)
    assert outcome.actions == ()
    assert "set the step or sprint PR target" in outcome.held[0].reason


def test_candidate_filter_excludes_anchor_quick_fix_and_open_task() -> None:
    values = (
        step(),
        step(bead_id="anchor", anchor=True),
        step(bead_id="quick", quick_fix=True),
        step(bead_id="closed", status=SprintState.CLOSED),
        step(bead_id="tasked"),
    )
    task = Task("tasked", "quality-mgr", "assigned", 1, NOW, NOW, "queued")
    assert tuple(item.bead_id for item in candidates(phase(*values), (task,))) == ("atm-bh-6.group-qa",)


def test_each_hold_is_flat_and_does_not_assign() -> None:
    conflict = Merge("sprint/bh-6", "sprint/bh-1", MergeState.CONFLICT, ("model.py",), (), NOW)
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
    pr = PullRequest(132, "https://example/pr/132", "sprint/bh-6", "sprint/bh-1", "abc123", CiState.FAIL_BUG, ("test",))
    seen: list[DispatchContext] = []

    def render(context: DispatchContext):
        seen.append(context)
        return Ok(DispatchSpec("qa", {"commit": context.commit}))

    outcome = run(step(), prs=(pr,), spec=render)
    assert outcome.held == ()
    assert outcome.actions[0].to == "quality-mgr"
    assert outcome.actions[0].spec == DispatchSpec("qa", {"commit": "abc123"})
    assert seen[0] == DispatchContext(
        step(), SPRINT, "atm-phase-bh", "Phase BH", "fenix", "abc123",
        "sprint/bh-1", "/worktrees/bh-6", pr, (pr,), REPO,
    )
    sanity = run(step(kind="sanity", sprint="bh-6", sprint_bead=None))
    assert sanity.actions[0].to == "dev-sanity"


def test_closed_pr_cannot_replace_an_open_dispatch_context() -> None:
    open_pr = PullRequest(132, "https://example/pr/132", "sprint/bh-6", "sprint/bh-1", "open", CiState.PASS, ())
    closed_pr = replace(open_pr, number=133, head_sha="closed", state=PrState.CLOSED)
    seen: list[DispatchContext] = []
    outcome = run(step(), prs=(open_pr, closed_pr), spec=lambda context: seen.append(context) or Ok(DispatchSpec("qa", {})))
    assert outcome.actions and seen[0].pr == open_pr and seen[0].layer_prs == (open_pr,)


def test_draft_pr_remains_in_the_dispatch_context() -> None:
    draft_pr = PullRequest(132, "https://example/pr/132", "sprint/bh-6", "sprint/bh-1", "draft", CiState.PASS, (), PrState.DRAFT)
    seen: list[DispatchContext] = []
    outcome = run(step(), prs=(draft_pr,), spec=lambda context: seen.append(context) or Ok(DispatchSpec("qa", {})))
    assert outcome.actions and seen[0].pr == draft_pr and seen[0].layer_prs == (draft_pr,)


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


def _context() -> DispatchContext:
    step = Step(
        "atm-bh-6.qa", "bh-6", "qa", False, False, SprintState.OPEN, True, None, "sprint/bh-6-tick", "integrate/phase-bh",
        "/worktrees/bh-6", 6, "normal", "bh-6", "atm-bh-6", None, None, "QA bh-6", "atm-bh-6.dev", "atm-bh-6", 1, None,
    )
    sprint = Sprint("atm-bh-6", "tick rules", 6, "sprint/bh-6-tick", "integrate/phase-bh", "phase-bh", (), False, 0)
    repo = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", "/policy", ("req-qa",), "/plans")
    pr = PullRequest(12, "https://example.invalid/pull/12", "sprint/bh-6-tick", "integrate/phase-bh", "abc123", CiState.PASS, ())
    return DispatchContext(step, sprint, "bh", "console", "fenix", "abc123", "integrate/phase-bh", "/worktrees/bh-6", pr, (pr,), repo)


def test_stack_has_one_owner_sprint() -> None:
    assert "stack" in {field.name for field in fields(Sprint)}
    assert "stack" not in {field.name for field in fields(DispatchContext)}


def test_qa_dispatch_reads_stack_from_sprint() -> None:
    spec = dispatch_spec(_context(), PHASE_TEMPLATES)
    assert isinstance(spec, Ok)
    assert spec.value.template == PHASE_TEMPLATES["qa"]
    assert spec.value.vars["stack"] == "phase-bh"
    assert spec.value.vars["layer"] == 6
