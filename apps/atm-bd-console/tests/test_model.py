from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atm_bd_console.codec import from_json, to_json
from atm_bd_console.model import _reconcile_problems, due, tick
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.types import (
    GhStatus,
    Inputs,
    Member,
    Merge,
    Phase,
    Problem,
    PullRequest,
    RemoteRef,
    RepoConfig,
    Sprint,
    State,
    Step,
    Task,
    Worktree,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
REPO = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", "/policy", (), "/plans")
STATE = State((), None, None, None, None, (), (), {})
FIXTURES = Path(__file__).parent / "fixtures" / "tick"


def step(bead_id: str, sprint: str, **changes) -> Step:
    value = Step(
        bead_id=bead_id, sprint=sprint, kind="dev", quick_fix=False, anchor=False, status="open", ready=False,
        assignee=None, branch=f"sprint/{sprint}", target="sprint/bh-1", worktree=f"/worktrees/{sprint}",
        layer=1, difficulty="normal", tag=sprint, top_epic="atm-phase-bh", claimed_at=None,
        closed_at=None, title=bead_id, checked_bead=None, sprint_bead=None, qa_round=None, carry_forward=None,
    )
    return replace(value, **changes)


def sprint(number: int, *, closed: bool = False, findings: int = 0, depends_on=()) -> Sprint:
    names = {1: "contract", 2: "viewer", 3: "source", 4: "mapper"}
    return Sprint(
        f"atm-bh-{number}", names[number], number, f"sprint/bh-{number}", "sprint/bh-1",
        "phase-bh", depends_on, closed, findings,
    )


def phase(*steps: Step, sprints=None, closed=False, step_errors=()) -> Phase:
    values = (sprint(1),) if sprints is None else sprints
    return Phase("atm-phase-bh", "phase-bh", "Phase BH", closed, values, tuple(steps), step_errors)


def inputs(value: Phase, **changes) -> Inputs:
    original = Inputs(
        NOW, "fenix", REPO, 60, Ok(value), Ok(()), Ok(()), Ok(()), Ok(()), (), Ok(()),
        GhStatus("off", None, None, None, False),
    )
    return replace(original, **changes)


def render(context):
    raise AssertionError(f"unexpected dispatch: {context.step.bead_id}")


def test_roster_statuses_and_markers() -> None:
    members = tuple(Member(name, presence, NOW - timedelta(minutes=5)) for name, presence in (
        ("busy", "active"), ("stalling", "idle"), ("blocked", "blocked"),
        ("offline", "offline"), ("idle", "idle"),
    ))
    tasks = (
        Task("foreign", "busy", "active", 1, NOW, NOW - timedelta(seconds=20), "foreign"),
        Task("broken", "busy", "assigned", 2, NOW, NOW, "broken"),
        Task("missing", "stalling", "assigned", 1, NOW, NOW, "missing"),
        Task("container", "blocked", "assigned", 1, NOW, NOW, "container"),
        Task("offline", "offline", "assigned", 1, NOW, NOW, "offline"),
    )
    value = phase(
        step("foreign", "atm-bh-1", top_epic="other-root"),
        step("broken", "atm-bh-1", top_epic=None),
        step("container", "atm-bh-1", kind="container"),
        step("offline", "atm-bh-1"),
    )
    snapshot, actions, _ = tick(inputs(value, members=Ok(members), tasks=Ok(tasks)), STATE, NOW, render)
    assert actions == ()
    assert tuple(agent.status for agent in snapshot.agents) == ("busy", "stalling", "blocked", "offline", "idle")
    assert tuple(row.marker for agent in snapshot.agents for row in agent.rows) == ("red", "red", "yellow", None, None)
    assert snapshot.agents[0].rows[0].state == "running"
    assert snapshot.agents[0].rows[0].age_s == 20


def test_due_cycle_fetch_missing_ref_and_merge_pairs() -> None:
    qa = step("qa", "atm-bh-1", kind="qa", ready=True, branch="missing", target="base")
    pushed = step("dev", "atm-bh-1", branch="present", target="base")
    value = phase(qa, pushed)
    remote = RemoteRef("present", "abc")
    state = replace(STATE, remotes=(remote,))
    assert due(state, value, "graphql", NOW) == due(state, value, "graphql", NOW)
    first = due(state, value, "graphql", NOW)
    assert first.fetch and first.github
    assert first.merges == (("missing", "base"), ("present", "base")) or first.merges == (("present", "base"), ("missing", "base"))
    at_five = due(replace(state, last_cycle=NOW), value, "graphql", NOW + timedelta(minutes=5))
    assert at_five.fetch and not at_five.github
    assert at_five.merges == (("missing", "base"),)
    at_ten = due(replace(state, last_cycle=NOW), value, "off", NOW + timedelta(minutes=10))
    assert at_ten.fetch and not at_ten.github
    no_missing = due(replace(state, last_cycle=NOW), phase(pushed), "graphql", NOW + timedelta(minutes=5))
    assert not no_missing.fetch and not no_missing.github


def test_per_recipient_problem_batches_survive_state_round_trip() -> None:
    def update(current: State, proposed: tuple[Problem, ...], at: datetime):
        problems, actions, batch_due, first_send, last_batch = _reconcile_problems(proposed, current, at, False)
        return replace(current, problems=problems, sent=(current.sent + actions)[-5:], batch_due=batch_due,
                       cycle_first_send=first_send, last_batch=last_batch), actions

    lead = [Problem(f"lead-{index}", f"bead-{index}", "fenix", f"lead {index}", NOW) for index in range(3)]
    assignee = Problem("assignee", "bead-qa", "solar", "assignee", NOW)
    state, actions = update(STATE, (lead[0],), NOW + timedelta(minutes=1))
    assert [action.kind for action in actions] == ["send"]
    state, actions = update(state, (lead[0], lead[1], assignee), NOW + timedelta(minutes=2))
    assert [(action.kind, action.to) for action in actions] == [("send", "fenix"), ("send", "solar")]
    state, actions = update(state, (*lead, assignee), NOW + timedelta(minutes=3))
    assert [(action.kind, action.to) for action in actions] == [("send", "fenix")]
    restored = from_json(State, to_json(state))
    assert isinstance(restored, Ok)
    state, actions = update(restored.value, (*lead, assignee), NOW + timedelta(minutes=5))
    assert actions == ()
    state, actions = update(state, (lead[0], lead[2], assignee), NOW + timedelta(minutes=11))
    assert [(action.kind, action.to) for action in actions] == [("batch", "fenix")]
    assert actions[0].bead_ids == ("bead-0", "bead-2")
    state, actions = update(state, (lead[0], lead[2], assignee), NOW + timedelta(minutes=12))
    assert [(action.kind, action.to) for action in actions] == [("batch", "solar")]
    state, actions = update(state, (lead[0], lead[2], assignee), NOW + timedelta(minutes=21))
    assert [(action.kind, action.to) for action in actions] == [("batch", "fenix")]
    assert len(state.sent) == 5


def test_pr_rows_are_layer_ordered_and_only_top_ci_is_a_problem() -> None:
    steps = tuple(step(f"dev-{number}", f"atm-bh-{number}", branch=f"branch-{number}", layer=number)
                  for number in (1, 2, 3))
    sprints = (sprint(1), sprint(2), sprint(3))
    prs = (
        PullRequest(3, "url3", "branch-3", "branch-2", "sha3", "failure", ("test",)),
        PullRequest(1, "url1", "branch-1", "base", "sha1", "success", ()),
        PullRequest(2, "url2", "branch-2", "branch-1", "sha2", "failure", ("lint",)),
    )
    snapshot, _, _ = tick(inputs(
        phase(*steps, sprints=sprints), prs=Ok(prs),
        remotes=Ok(tuple(RemoteRef(item.head, item.head_sha) for item in prs)),
        gh=GhStatus("graphql", True, None, NOW, False),
    ), STATE, NOW, render)
    assert [row.layer for row in snapshot.prs] == [1, 2, 3]
    assert [row.top for row in snapshot.prs] == [False, False, True]
    assert snapshot.top_pr == 3 and snapshot.top_ci == "failure"
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha3"]


def test_missing_pr_only_after_successful_fresh_github_read() -> None:
    value = phase(step("dev", "atm-bh-1", branch="pushed"))
    base = inputs(value, remotes=Ok((RemoteRef("pushed", "sha"),)))
    success = replace(base, gh=GhStatus("graphql", True, None, NOW, False))
    snapshot, _, _ = tick(success, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:dev"]
    for gh in (GhStatus("off", None, None, None, False), GhStatus("graphql", False, NotFound("not_found", "gh", "retry"), NOW, False)):
        snapshot, _, _ = tick(replace(base, gh=gh), STATE, NOW, render)
        assert not any(problem.key.startswith("missing_pr") for problem in snapshot.problems)
    closed = inputs(phase(step("dev", "atm-bh-1", branch="pushed", status="closed")),
                    remotes=Ok((RemoteRef("pushed", "sha"),)), gh=success.gh)
    snapshot, _, _ = tick(closed, STATE, NOW, render)
    assert not any(problem.key.startswith("missing_pr") for problem in snapshot.problems)


def test_stop_rules_metrics_mermaid_and_sent_window() -> None:
    steps = (
        step("dev-1", "atm-bh-1", status="closed", claimed_at=NOW - timedelta(seconds=100), closed_at=NOW - timedelta(seconds=50)),
        step("dev-2", "atm-bh-2", status="closed", claimed_at=NOW - timedelta(seconds=75), closed_at=NOW - timedelta(seconds=25)),
        step("atm-bh-3.group-dev", "atm-bh-3", status="in_progress", assignee="solar", claimed_at=NOW - timedelta(seconds=20)),
        step("atm-bh-4.group-dev", "atm-bh-4", ready=True),
    )
    sprints = (sprint(1, closed=True, findings=3), sprint(2, closed=True, findings=1),
               sprint(3, findings=0, depends_on=("atm-bh-1",)), sprint(4, findings=3, depends_on=("atm-bh-1",)))
    value = phase(*steps, sprints=sprints)
    snapshot, _, new_state = tick(inputs(value), STATE, NOW, render)
    assert (snapshot.metrics.done, snapshot.metrics.total, snapshot.metrics.pct, snapshot.metrics.active_s) == (2, 6, 33, 95)
    assert snapshot.mermaid == (FIXTURES / "mermaid.txt").read_text().rstrip("\n")
    assert [view.state for view in snapshot.sprints] == ["closed", "closed", "in_progress", "ready"]
    assert snapshot.stopped is None and new_state.idle_since is None
    closed, actions, _ = tick(inputs(replace(value, closed=True)), STATE, NOW, render)
    assert closed.stopped == "closed" and actions == ()
    idle_value = phase(step("blocked", "atm-bh-1"))
    idle, actions, _ = tick(inputs(idle_value), replace(STATE, idle_since=NOW - timedelta(minutes=61)), NOW, render)
    assert idle.stopped == "idle" and actions == ()
    running = phase(step("running", "atm-bh-1", status="in_progress", claimed_at=NOW - timedelta(hours=2)))
    running_snapshot, _, _ = tick(inputs(running), replace(STATE, idle_since=NOW - timedelta(minutes=61)), NOW, render)
    assert running_snapshot.stopped is None


def test_all_input_and_step_errors_reach_snapshot_without_exception() -> None:
    error = NotFound("not_found", "input", "repair it")
    value = phase(step_errors=(("bad", error),))
    base = inputs(value)
    for name in ("phase", "members", "tasks", "worktrees", "remotes", "prs"):
        snapshot, _, _ = tick(replace(base, **{name: Err(error)}), STATE, NOW, lambda context: Err(error))
        assert error in snapshot.errors
    snapshot, _, _ = tick(replace(base, merges=(Err(error),)), STATE, NOW, lambda context: Err(error))
    assert error in snapshot.errors
    snapshot, actions, _ = tick(base, STATE, NOW, lambda context: Err(error))
    assert error in snapshot.errors
    assert actions == ()


def test_task_read_error_never_causes_duplicate_assignment() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, branch="sprint/bh-1",
                     worktree="/worktrees/atm-bh-1")
    base = inputs(phase(candidate), tasks=Err(NotFound("not_found", "tasks", "retry")),
                  remotes=Ok((RemoteRef("sprint/bh-1", "sha"),)),
                  worktrees=Ok((Worktree("/worktrees/atm-bh-1", "sprint/bh-1", "sha"),)))
    snapshot, actions, _ = tick(base, STATE, NOW, render)
    assert actions == ()
    assert any(isinstance(error, NotFound) and error.what == "tasks" for error in snapshot.errors)


def test_rejected_step_is_not_dispatched_even_if_source_retains_it() -> None:
    error = NotFound("not_found", "checked_bead", "repair metadata")
    candidate = step("bad-qa", "atm-bh-1", kind="qa", ready=True, branch="sprint/bh-1",
                     worktree="/worktrees/atm-bh-1")
    value = phase(candidate, step_errors=((candidate.bead_id, error),))
    supplied = inputs(value, remotes=Ok((RemoteRef("sprint/bh-1", "sha"),)),
                      worktrees=Ok((Worktree("/worktrees/atm-bh-1", "sprint/bh-1", "sha"),)))
    snapshot, actions, _ = tick(supplied, STATE, NOW, render)
    assert actions == ()
    assert error in snapshot.errors
    assert all(view.bead_id != candidate.bead_id for sprint_view in snapshot.sprints for view in sprint_view.steps)


def test_hold_is_shown_and_sent_once_then_batched() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, assignee="solar", branch="missing")
    supplied = inputs(phase(candidate))
    snapshot, actions, state = tick(supplied, STATE, NOW, render)
    assert snapshot.sections[0].label == "Held"
    assert snapshot.sections[0].rows[0].bead_id == "qa"
    assert [(action.kind, action.to) for action in actions] == [("send", "solar")]
    _, actions, state = tick(supplied, state, NOW + timedelta(minutes=5), render)
    assert actions == ()
    _, actions, _ = tick(supplied, state, NOW + timedelta(minutes=10), render)
    assert [(action.kind, action.to) for action in actions] == [("batch", "solar")]


def test_conflict_problem_names_files_and_spec_error_is_snapshot_only() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, assignee="solar", branch="sprint/bh-1",
                     worktree="/worktrees/atm-bh-1")
    common = inputs(phase(candidate), remotes=Ok((RemoteRef("sprint/bh-1", "sha"),)),
                    worktrees=Ok((Worktree("/worktrees/atm-bh-1", "sprint/bh-1", "sha"),)))
    conflict = Merge("sprint/bh-1", "sprint/bh-1", "conflict", ("model.py",), (), NOW)
    snapshot, actions, _ = tick(replace(common, merges=(Ok(conflict),)), STATE, NOW, render)
    assert [(action.kind, action.to) for action in actions] == [("send", "solar")]
    assert "model.py" in snapshot.problems[0].text

    error = NotFound("not_found", "checked_bead", "repair metadata")
    snapshot, actions, _ = tick(common, STATE, NOW, lambda context: Err(error))
    assert actions == ()
    assert error in snapshot.errors


def test_pure_modules_contain_no_source_calls_or_imports() -> None:
    source_dir = Path(__file__).parents[1] / "src" / "atm_bd_console"
    forbidden = ("subprocess", "open(", "bd ", "atm ", "gh ", "stage:", "metadata", "headRefName")
    for name in ("model.py", "dispatch.py"):
        source = (source_dir / name).read_text()
        assert all(token not in source for token in forbidden)
        assert "_source import" not in source
        assert "import atm_bd" not in source
