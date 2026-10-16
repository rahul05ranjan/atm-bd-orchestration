from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atm_bd_console.codec import from_json, to_json
from atm_bd_console.dispatch import sprint_of
from atm_bd_console.model import _reconcile_problems, _sprint_views, due, tick
from atm_bd_console.result import CommandFailed, Err, NotFound, Ok, Timeout
from atm_bd_console.types import (
    Bead,
    DispatchSpec,
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
        GhStatus("off", None, None, None, False), Ok(()),
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


def test_agent_status_uses_presence_when_task_state_disagrees() -> None:
    members = (
        Member("presence-idle", "idle", NOW - timedelta(minutes=5)),
        Member("presence-active", "active", NOW - timedelta(minutes=5)),
    )
    tasks = (
        Task("running", "presence-idle", "active", 1, NOW, NOW - timedelta(seconds=20), "running"),
        Task("queued", "presence-active", "assigned", 1, NOW, NOW, "queued"),
    )
    value = phase(step("running", "atm-bh-1"), step("queued", "atm-bh-1"))
    snapshot, actions, _ = tick(inputs(value, members=Ok(members), tasks=Ok(tasks)), STATE, NOW, render)

    assert actions == ()
    assert tuple(agent.status for agent in snapshot.agents) == ("stalling", "busy")
    assert tuple(agent.rows[0].state for agent in snapshot.agents) == ("running", "queued")


def test_existing_out_of_phase_bead_is_red_and_missing_bead_is_yellow() -> None:
    member = Member("solar", "active", NOW)
    tasks = (
        Task("foreign", "solar", "active", 1, NOW, NOW, "foreign task"),
        Task("missing", "solar", "assigned", 2, NOW, NOW, "missing bead"),
    )
    foreign = Bead("foreign", "foreign task", "open", (), "other-root", (), "solar", {}, NOW, None, None)
    supplied = inputs(phase(), members=Ok((member,)), tasks=Ok(tasks), task_beads=Ok((foreign,)))
    snapshot, _, _ = tick(supplied, STATE, NOW, render)
    assert [row.marker for row in snapshot.agents[0].rows] == ["red", "yellow"]

    error = NotFound("not_found", "task bead read", "retry")
    snapshot, _, _ = tick(replace(supplied, task_beads=Err(error)), STATE, NOW, render)
    assert [row.marker for row in snapshot.agents[0].rows] == [None, None]
    assert error in snapshot.errors


def test_due_cycle_fetch_missing_ref_and_merge_pairs() -> None:
    qa = step("qa", "atm-bh-1", kind="qa", ready=True, branch="missing", target="base")
    pushed = step("dev", "atm-bh-1", branch="present", target="base")
    value = phase(qa, pushed)
    remote = RemoteRef("present", "abc")
    state = replace(STATE, remotes=(remote,))
    first = due(state, value, "atm", "graphql", NOW)
    assert first.fetch is True
    assert first.github is True
    assert first.merges == (("present", "base"), ("missing", "base"))
    at_five = due(replace(state, last_cycle=NOW), value, "atm", "graphql", NOW + timedelta(minutes=5))
    assert at_five.fetch and not at_five.github
    assert at_five.merges == (("missing", "base"),)
    at_ten = due(replace(state, last_cycle=NOW), value, "atm", "off", NOW + timedelta(minutes=10))
    assert at_ten.fetch and not at_ten.github
    no_missing = due(replace(state, last_cycle=NOW), phase(pushed), "atm", "graphql", NOW + timedelta(minutes=5))
    assert not no_missing.fetch and not no_missing.github
    inherited = due(STATE, phase(step("qa", "bh-1", kind="qa", ready=True, branch="branch", target=None)),
                    "atm", "graphql", NOW)
    assert inherited.merges == (("branch", "sprint/bh-1"),)


def test_due_excludes_ready_steps_with_open_tasks() -> None:
    ready = step("qa", "atm-bh-1", kind="qa", ready=True, branch="missing", target="base")
    task = Task("qa", "trinity", "active", 0, NOW, NOW, "running")

    assert due(replace(STATE, last_cycle=NOW), phase(ready), "atm", "graphql", NOW + timedelta(minutes=5), (task,)).fetch is False


def test_one_sprint_lookup_for_due_dispatch_and_sprint_views() -> None:
    # A foreign sprint whose id ends with the step's tag used to win due's endswith match while dispatch and the
    # sprint views used the prefixed id: one step, two targets.
    foreign = replace(sprint(1), id="other-bh-1", target="sprint/other")
    own = replace(sprint(1), target="sprint/own")
    tagged = step("qa", "bh-1", kind="qa", ready=True, branch="branch", target=None)
    value = phase(tagged, sprints=(foreign, own))
    assert sprint_of(tagged, value, "atm") == own
    assert due(STATE, value, "atm", "graphql", NOW).merges == (("branch", "sprint/own"),)
    views, _ = _sprint_views(value, "atm")
    assert [[step_view.bead_id for step_view in view.steps] for view in views] == [[], ["qa"]]
    # The sprint bead id wins over the tag, the same for every caller.
    by_bead = replace(tagged, sprint="unknown", sprint_bead="other-bh-1")
    assert sprint_of(by_bead, phase(by_bead, sprints=(foreign, own)), "atm") == foreign
    assert due(STATE, phase(by_bead, sprints=(foreign, own)), "atm", "graphql", NOW).merges == (
        ("branch", "sprint/other"),
    )


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


def test_sprint_layer_above_holding_no_step_takes_its_sprint_layer_and_top() -> None:
    # sprint/bh-3 is a stack layer no step's branch names: its row comes from the sprint, not a step owner.
    steps = tuple(step(f"dev-{number}", f"atm-bh-{number}", branch=f"branch-{number}", layer=number)
                  for number in (1, 2))
    prs = (
        PullRequest(1, "url1", "sprint/bh-3", "branch-2", "sha3", "success", ()),
        PullRequest(3, "url3", "branch-2", "branch-1", "sha2", "success", ()),
        PullRequest(2, "url2", "branch-1", "base", "sha1", "success", ()),
    )
    snapshot, _, _ = tick(inputs(
        phase(*steps, sprints=(sprint(1), sprint(2), sprint(3))), prs=Ok(prs),
        remotes=Ok(tuple(RemoteRef(item.head, item.head_sha) for item in prs)),
        gh=GhStatus("graphql", True, None, NOW, False),
    ), STATE, NOW, render)
    assert [(row.pr.number, row.layer, row.bead_id, row.top) for row in snapshot.prs] == [
        (2, 1, "dev-1", False), (3, 2, "dev-2", False), (1, 3, "atm-bh-3", True),
    ]
    assert snapshot.top_pr == 1


def test_missing_pr_only_after_successful_fresh_github_read() -> None:
    value = phase(step("dev", "atm-bh-1", branch="pushed"))
    base = inputs(value, remotes=Ok((RemoteRef("pushed", "sha"),)))
    success = replace(base, gh=GhStatus("graphql", True, None, NOW, False))
    snapshot, _, _ = tick(success, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]
    for gh in (GhStatus("off", None, None, None, False), GhStatus("graphql", False, NotFound("not_found", "gh", "retry"), NOW, False)):
        snapshot, _, _ = tick(replace(base, gh=gh), STATE, NOW, render)
        assert not any(problem.key.startswith("missing_pr") for problem in snapshot.problems)
    closed = inputs(phase(step("dev", "atm-bh-1", branch="pushed", status="closed")),
                    remotes=Ok((RemoteRef("pushed", "sha"),)), gh=success.gh)
    snapshot, _, _ = tick(closed, STATE, NOW, render)
    assert not any(problem.key.startswith("missing_pr") for problem in snapshot.problems)


def test_read_derived_problems_survive_missing_reads_without_duplicate_sends() -> None:
    value = phase(step("dev", "atm-bh-1", branch="pushed", assignee="solar"))
    remote = RemoteRef("pushed", "sha")
    first = inputs(value, remotes=Ok((remote,)), prs=Ok(()),
                   gh=GhStatus("graphql", True, None, NOW, False))
    snapshot, actions, state = tick(first, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]
    assert [(action.kind, action.to) for action in actions] == [("send", "solar")]
    assert state.batch_due["solar"] == NOW + timedelta(minutes=10)

    unread = replace(first, prs=None, gh=GhStatus("off", None, None, None, False))
    snapshot, actions, state = tick(unread, state, NOW + timedelta(minutes=5), render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]
    assert snapshot.problems[0].first_seen == NOW
    assert actions == ()
    assert state.batch_due["solar"] == NOW + timedelta(minutes=10)

    snapshot, actions, state = tick(replace(first, gh=GhStatus("graphql", True, None,
                                                              NOW + timedelta(minutes=10), False)),
                                    state, NOW + timedelta(minutes=10), render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]
    assert [action.kind for action in actions] == ["batch"]
    assert state.batch_due["solar"] == NOW + timedelta(minutes=20)

    found_pr = PullRequest(1, "url", "pushed", "base", "sha", "success", ())
    snapshot, actions, state = tick(replace(first, prs=Ok((found_pr,)),
                                            gh=GhStatus("graphql", True, None,
                                                        NOW + timedelta(minutes=20), False)),
                                    state, NOW + timedelta(minutes=20), render)
    assert snapshot.problems == ()
    assert actions == ()


def test_ci_and_conflict_problems_survive_until_their_sources_are_refreshed() -> None:
    value = phase(step("dev", "atm-bh-1", branch="pushed", assignee="solar"))
    failed_pr = PullRequest(1, "url", "pushed", "base", "sha", "failure", ("lint",))
    first = inputs(value, prs=Ok((failed_pr,)), gh=GhStatus("graphql", True, None, NOW, False))
    snapshot, _, state = tick(first, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha"]
    unread = replace(first, prs=Err(NotFound("not_found", "prs", "retry")),
                     gh=GhStatus("graphql", False, None, NOW, False))
    snapshot, actions, state = tick(unread, state, NOW + timedelta(minutes=5), render)
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha"]
    assert actions == ()
    clean_pr = replace(failed_pr, ci="success", failed_checks=())
    snapshot, _, _ = tick(replace(first, prs=Ok((clean_pr,)),
                                  gh=GhStatus("graphql", True, None, NOW + timedelta(minutes=10), False)),
                          state, NOW + timedelta(minutes=10), render)
    assert snapshot.problems == ()

    conflict = Merge("pushed", "base", "conflict", ("model.py",), (), NOW)
    with_merge = replace(first, prs=None, gh=GhStatus("off", None, None, None, False), merges=(Ok(conflict),))
    snapshot, _, state = tick(with_merge, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["conflict:pushed"]
    snapshot, actions, state = tick(replace(with_merge, merges=(Err(NotFound("not_found", "merge", "retry")),)),
                                    state, NOW + timedelta(minutes=5), render)
    assert [problem.key for problem in snapshot.problems] == ["conflict:pushed"]
    assert actions == ()
    clean_merge = replace(conflict, state="clean", files=())
    snapshot, _, _ = tick(replace(with_merge, merges=(Ok(clean_merge),)), state,
                          NOW + timedelta(minutes=10), render)
    assert snapshot.problems == ()


def test_shared_branch_reports_one_problem_per_kind_to_work_owner() -> None:
    dev = step("dev", "atm-bh-1", branch="shared", assignee="solar", status="closed")
    qa = step("qa", "atm-bh-1", kind="qa", branch="shared", assignee=None)
    qa_task = Task("qa", "quality-mgr", "assigned", 1, NOW, NOW, "review")
    conflict = Merge("shared", "base", "conflict", ("model.py",), (), NOW)
    for steps in ((dev, qa), (qa, dev)):
        supplied = inputs(
            phase(*steps), tasks=Ok((qa_task,)), remotes=Ok((RemoteRef("shared", "sha"),)),
            merges=(Ok(conflict),), prs=Ok(()), gh=GhStatus("graphql", True, None, NOW, False),
        )
        snapshot, actions, _ = tick(supplied, STATE, NOW, render)
        assert [problem.key for problem in snapshot.problems] == ["conflict:shared", "missing_pr:shared"]
        assert [(problem.bead_id, problem.to) for problem in snapshot.problems] == [("dev", "solar")] * 2
        assert [(action.kind, action.to) for action in actions] == [("send", "solar")] * 2


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
    for name in ("phase", "members", "tasks", "worktrees", "remotes", "prs", "task_beads"):
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
    assert [(action.kind, action.to) for action in actions] == [("send", "solar"), ("send", "solar")]
    _, actions, state = tick(supplied, state, NOW + timedelta(minutes=5), render)
    assert actions == ()
    _, actions, _ = tick(supplied, state, NOW + timedelta(minutes=10), render)
    assert [(action.kind, action.to) for action in actions] == [("batch", "solar")]


def test_not_ready_hold_is_visible_without_problem_or_notice() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=False, assignee=None, branch="missing")
    supplied = inputs(phase(candidate))
    snapshot, actions, state = tick(supplied, STATE, NOW, render)
    assert len(snapshot.sections) == 1
    assert snapshot.sections[0].label == "Held"
    assert snapshot.sections[0].rows[0].reason == "not ready"
    assert snapshot.problems == ()
    assert actions == ()

    snapshot, actions, state = tick(supplied, state, NOW + timedelta(minutes=10), render)
    assert snapshot.sections[0].rows[0].reason == "not ready"
    assert snapshot.problems == ()
    assert actions == ()


def test_open_fix_and_future_poured_sanity_and_qa_do_not_notify_missing_metadata() -> None:
    fix = step("atm-bh-1.fix", "atm-bh-1", kind="fix", ready=True, branch="fix/bh-1", assignee="solar")
    future = tuple(step(
        f"{fix.bead_id}.{kind}", "atm-bh-1", kind=kind, ready=False, branch=None, worktree=None,
        checked_bead=fix.bead_id, sprint_bead="atm-bh-1",
    ) for kind in ("sanity", "qa"))
    supplied = inputs(phase(fix, *future))
    state = STATE
    for minutes in (0, 10, 20):
        snapshot, actions, state = tick(supplied, state, NOW + timedelta(minutes=minutes), render)
        held = snapshot.sections[0].rows
        assert {row.bead_id for row in held if row.reason == "not ready"} == {step.bead_id for step in future}
        assert any("<missing>" in row.reason for row in held)
        assert snapshot.problems == ()
        assert snapshot.errors == ()
        assert actions == ()
        assert state.sent == ()
        assert state.batch_due == {}


@pytest.mark.parametrize("branch", (None, ""))
def test_ready_step_without_branch_reports_one_meaningful_configuration_problem(branch: str | None) -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, branch=branch, worktree=None)
    supplied = inputs(phase(candidate))
    snapshot, actions, state = tick(supplied, STATE, NOW, render)
    assert len(snapshot.sections[0].rows) == 2
    assert snapshot.problems == (Problem("hold:qa", "qa", "fenix", "missing branch configuration", NOW),)
    assert len(actions) == 1
    assert (actions[0].kind, actions[0].to, actions[0].text) == ("send", "fenix", "missing branch configuration")
    assert all("<missing>" not in problem.text for problem in snapshot.problems)
    snapshot, actions, _ = tick(supplied, state, NOW + timedelta(minutes=5), render)
    assert len(snapshot.problems) == 1
    assert actions == ()


def test_missing_worktree_keeps_conflict_details_without_duplicate_notices() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, assignee="solar", branch="topic", target="base")
    conflict = Merge("topic", "base", "conflict", ("model.py", "dispatch.py"), (), NOW)
    supplied = inputs(phase(candidate), remotes=Ok((RemoteRef("topic", "sha"),)),
                      worktrees=Ok(()), merges=(Ok(conflict),))

    snapshot, actions, state = tick(supplied, STATE, NOW, render)

    conflict_text = "merge conflict with base: model.py, dispatch.py"
    assert snapshot.sections[0].label == "Held"
    assert [row.reason for row in snapshot.sections[0].rows] == ["no worktree for topic", conflict_text]
    assert [problem.key for problem in snapshot.problems] == ["hold:qa", "conflict:topic"]
    assert [problem.text for problem in snapshot.problems] == ["no worktree for topic", conflict_text]
    assert [(action.kind, action.to, action.text) for action in actions] == [
        ("send", "solar", "no worktree for topic"), ("send", "solar", conflict_text),
    ]

    refreshed = replace(supplied, merges=(Ok(replace(conflict, checked_at=NOW + timedelta(minutes=5))),))
    snapshot, actions, _ = tick(refreshed, state, NOW + timedelta(minutes=5), render)

    assert [problem.key for problem in snapshot.problems] == ["hold:qa", "conflict:topic"]
    assert all(problem.first_seen == NOW for problem in snapshot.problems)
    assert actions == ()

    recovered_worktree = replace(refreshed, worktrees=Ok((Worktree(candidate.worktree, "topic", "sha"),)))
    snapshot, actions, _ = tick(recovered_worktree, state, NOW + timedelta(minutes=6), render)

    assert [problem.key for problem in snapshot.problems] == ["conflict:topic"]
    assert snapshot.problems[0].text == conflict_text
    assert snapshot.problems[0].first_seen == NOW
    assert actions == ()


def test_conflict_problem_names_files_and_spec_error_is_snapshot_only() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, assignee="solar", branch="sprint/bh-1",
                     worktree="/worktrees/atm-bh-1")
    common = inputs(phase(candidate), remotes=Ok((RemoteRef("sprint/bh-1", "sha"),)),
                    worktrees=Ok((Worktree("/worktrees/atm-bh-1", "sprint/bh-1", "sha"),)))
    conflict = Merge("sprint/bh-1", "sprint/bh-1", "conflict", ("model.py",), (), NOW)
    snapshot, actions, _ = tick(replace(common, merges=(Ok(conflict),)), STATE, NOW, render)
    assert [(action.kind, action.to) for action in actions] == [("send", "solar")]
    assert [problem.key for problem in snapshot.problems] == ["conflict:sprint/bh-1"]
    assert "model.py" in snapshot.problems[0].text

    error = NotFound("not_found", "checked_bead", "repair metadata")
    verified = replace(common, merges=(Ok(replace(conflict, state="clean", files=())),))
    snapshot, actions, _ = tick(verified, STATE, NOW, lambda context: Err(error))
    assert actions == ()
    assert error in snapshot.errors


@pytest.mark.parametrize("error", (
    NotFound("not_found", "origin/base", "fetch the named branch and retry"),
    Timeout("timeout", ("git", "merge-tree"), 30, "retry the merge check"),
    CommandFailed("command_failed", ("git", "merge-tree"), 129, "unknown option write-tree", "upgrade git"),
))
def test_merge_probe_error_holds_with_problem_then_recovers(error) -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, branch="topic", target="base")
    supplied = inputs(phase(candidate), remotes=Ok((RemoteRef("topic", "sha"),)),
                      worktrees=Ok((Worktree(candidate.worktree, "topic", "sha"),)), merges=(Err(error),))
    snapshot, actions, state = tick(supplied, STATE, NOW, render)
    assert error in snapshot.errors
    assert not any(action.kind == "assign" for action in actions)
    assert error.kind in snapshot.sections[0].rows[0].reason
    assert error.fix in snapshot.problems[0].text
    if isinstance(error, NotFound):
        assert error.what in snapshot.problems[0].text
    elif isinstance(error, CommandFailed):
        assert error.stderr in snapshot.problems[0].text
    elif isinstance(error, Timeout):
        assert "timed out after 30s" in snapshot.problems[0].text
    verified = replace(supplied, merges=(Ok(Merge("topic", "base", "clean", (), (), NOW)),))
    recovered, actions, _ = tick(verified, state, NOW, lambda context: Ok(DispatchSpec("qa", {})))
    assert [action.kind for action in actions] == ["assign"]
    assert recovered.sections == () and recovered.problems == ()


def test_ready_candidate_refreshes_even_when_origin_ref_is_cached() -> None:
    candidate = step("qa", "atm-bh-1", kind="qa", ready=True, branch="topic", target="base")
    state = replace(STATE, last_cycle=NOW, remotes=(RemoteRef("topic", "cached"),))
    assert due(state, phase(candidate), "atm", "off", NOW).fetch


def test_pure_modules_contain_no_source_calls_or_imports() -> None:
    source_dir = Path(__file__).parents[1] / "src" / "atm_bd_console"
    forbidden = ("subprocess", "open(", "bd ", "atm ", "gh ", "stage:", "metadata", "headRefName")
    for name in ("model.py", "dispatch.py"):
        source = (source_dir / name).read_text()
        assert all(token not in source for token in forbidden)
        assert "_source import" not in source
        assert "import atm_bd" not in source
