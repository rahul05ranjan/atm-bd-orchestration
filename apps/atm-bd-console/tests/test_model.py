from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.cells import cell_len

from atm_bd_console.codec import from_json, to_json
from atm_bd_console.dispatch import sprint_of
from atm_bd_console.enums import CiState, MergeState, PrState, SprintState
from atm_bd_console.icons import icon_for
from atm_bd_console.model import _reconcile_problems, _sprint_views, due, qa_state_from_close, tick
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

PHASE_BH_SPRINTS = (
    ("atm-bh-1", "bh-1: contract: Result, source and tick types, JSON codec", ()),
    ("atm-bh-2", "bh-2: bead-queues as a snapshot viewer, install check", ()),
    ("atm-bh-3", "bh-3: ATM source: members, tasks, presence, send, assign", ("atm-bh-1",)),
    ("atm-bh-4", "bh-4: beads source and the atm-bd phase and formula mapping", ("atm-bh-1",)),
    ("atm-bh-5", "bh-5: git and GitHub sources: refs, merge check, worktrees, open PRs", ("atm-bh-1",)),
    ("atm-bh-6", "bh-6: tick rules: snapshot, holds, problems, agent status, metrics, stops", ("atm-bh-1",)),
    ("atm-bh-7", "bh-7: runtime files: JSON state, lock, config, logging", ("atm-bh-1",)),
    ("atm-bh-8", "bh-8: renderers: dashboard, status bar, Status DAG, PRs", ("atm-bh-1",)),
    ("atm-bh-9", "bh-9: runner.py, cli.py (headless, show), MVP gates", ("atm-bh-3", "atm-bh-4", "atm-bh-5", "atm-bh-6", "atm-bh-7", "atm-bh-8")),
    ("atm-bh-10", "bh-10: Textual app: Dispatch, Status DAG, PRs tabs", ("atm-bh-9",)),
)


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
        GhStatus("graphql", True, None, None, False), Ok(()),
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


def test_qa_state_uses_active_qa_row_then_latest_closed_planned_qa_reason() -> None:
    assert qa_state_from_close("PASS: 0 findings filed") == "pass"
    assert qa_state_from_close("PASS: 2 carried findings confirmed") == "pass"
    assert qa_state_from_close("FAIL: 3 findings filed") == "fail"
    assert qa_state_from_close("needs follow-up") is None
    older = step("qa-old", "atm-bh-1", kind="qa", status="closed", assignee="quality-mgr",
                 closed_at=NOW - timedelta(minutes=2), close_reason="FAIL old")
    newer = step("qa-new", "atm-bh-1", kind="qa", status="closed", assignee="quality-mgr",
                 closed_at=NOW - timedelta(minutes=1), close_reason="PASS current")
    qa = Member("quality-mgr", "idle", NOW, agent_type="qa")
    snapshot, _, _ = tick(inputs(phase(older, newer), members=Ok((qa,))), STATE, NOW, render)
    assert snapshot.agents[0].qa_state == "pass"
    active = step("qa-active", "atm-bh-1", kind="qa", status="in_progress", assignee="quality-mgr")
    task = Task("qa-active", "quality-mgr", "active", 0, NOW, NOW, "QA")
    snapshot, _, _ = tick(inputs(phase(older, newer, active), members=Ok((qa,)), tasks=Ok((task,))), STATE, NOW, render)
    assert snapshot.agents[0].qa_state == "running"


@pytest.mark.parametrize(
    ("reason", "expected_state", "expected_color"),
    (
        ("PASS: 0 findings filed", "pass", "#2EA043"),
        ("PASS: 2 carried findings confirmed", "pass", "#2EA043"),
        ("FAIL: 3 findings filed", "fail", "#F85149"),
    ),
)
def test_qa_member_steps_from_running_to_closed_result(reason, expected_state, expected_color) -> None:
    qa = Member("quality-mgr", "active", NOW, agent_type="qa")
    active = step("atm-bi-5.group-qa", "atm-bi-5", kind="qa", status="in_progress", assignee=qa.name)
    task = Task(active.bead_id, qa.name, "active", 0, NOW, NOW, "QA review")

    running, _, _ = tick(
        inputs(phase(active), members=Ok((qa,)), tasks=Ok((task,))), STATE, NOW, render,
    )
    assert running.agents[0].qa_state == "running"
    assert icon_for(running.agents[0]) == ("\U000f029b", "#4A5AF8")

    closed = replace(active, status="closed", closed_at=NOW, close_reason=reason)
    result, _, _ = tick(inputs(phase(closed), members=Ok((qa,))), STATE, NOW, render)
    assert result.agents[0].qa_state == expected_state
    assert icon_for(result.agents[0]) == ("\U000f029b", expected_color)


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


def test_recorded_phase_bh_dag_labels_are_compact_and_keep_all_edges() -> None:
    """The recorded ten-sprint phase-BH snapshot that exposed the wide graph."""
    sprints = tuple(
        Sprint(sprint_id, title, number, f"sprint/bh-{number}", "base", "phase-bh", depends_on, True, 0)
        for number, (sprint_id, title, depends_on) in enumerate(PHASE_BH_SPRINTS, start=1)
    )
    views, mermaid = _sprint_views(phase(sprints=sprints), "atm")

    labels = [line.split('["', 1)[1].rsplit('"]', 1)[0] for line in mermaid.splitlines() if '["' in line]
    assert len(labels) == 10
    for label in labels:
        lines = label.split("<br/>")
        assert 1 <= len(lines) <= 3
        assert all(cell_len(line) <= 30 for line in lines)
    assert [view.title for view in views] == [title for _, title, _ in PHASE_BH_SPRINTS]
    assert [line.strip() for line in mermaid.splitlines() if " --> " in line] == [
        "atm_bh_1 --> atm_bh_3", "atm_bh_1 --> atm_bh_4", "atm_bh_1 --> atm_bh_5",
        "atm_bh_1 --> atm_bh_6", "atm_bh_1 --> atm_bh_7", "atm_bh_1 --> atm_bh_8",
        "atm_bh_3 --> atm_bh_9", "atm_bh_4 --> atm_bh_9", "atm_bh_5 --> atm_bh_9",
        "atm_bh_6 --> atm_bh_9", "atm_bh_7 --> atm_bh_9", "atm_bh_8 --> atm_bh_9",
        "atm_bh_9 --> atm_bh_10",
    ]


def test_dag_label_boundaries_handle_long_assignees_and_unbroken_unicode_words() -> None:
    title = "bh-8: renderers with a deliberately long description that must be truncated after two concise lines"
    active = step("atm-bh-8.group-dev", "atm-bh-8", status="in_progress", assignee="a-very-long-assignee-name")
    unicode_title = "bh-9: 測試 supercalifragilisticexpialidociousunbrokenword"
    value = phase(active, sprints=(
        Sprint("atm-bh-8", title, 8, "sprint/bh-8", "base", "phase-bh", (), False, 0),
        Sprint("atm-bh-9", unicode_title, 9, "sprint/bh-9", "base", "phase-bh", (), False, 0),
    ))

    views, mermaid = _sprint_views(value, "atm")
    labels = [line.split('["', 1)[1].rsplit('"]', 1)[0] for line in mermaid.splitlines() if '["' in line]
    assert labels
    for label in labels:
        lines = label.split("<br/>")
        assert 1 <= len(lines) <= 3
        assert all(cell_len(line) <= 30 for line in lines)
    assert labels[0].startswith("\U000f051f bh-8 dev:·")
    assert labels[0].endswith("…")
    assert views[0].title == title and views[1].title == unicode_title
    assert views[0].steps[0].bead_id == active.bead_id


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
        PullRequest(3, "url3", "branch-3", "branch-2", "sha3", CiState.FAIL_BUG, ("test",)),
        PullRequest(1, "url1", "branch-1", "base", "sha1", CiState.PASS, ()),
        PullRequest(2, "url2", "branch-2", "branch-1", "sha2", CiState.FAIL_BUG, ("lint",)),
    )
    snapshot, _, _ = tick(inputs(
        phase(*steps, sprints=sprints), prs=Ok(prs),
        remotes=Ok(tuple(RemoteRef(item.head, item.head_sha) for item in prs)),
        gh=GhStatus("graphql", True, None, NOW, False),
    ), STATE, NOW, render)
    assert [row.layer for row in snapshot.prs] == [1, 2, 3]
    assert [row.top for row in snapshot.prs] == [False, False, True]
    assert snapshot.top_pr == 3 and snapshot.top_ci == CiState.FAIL_BUG
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha3"]


def test_sprint_layer_above_holding_no_step_takes_its_sprint_layer_and_top() -> None:
    # sprint/bh-3 is a stack layer no step's branch names: its row comes from the sprint, not a step owner.
    steps = tuple(step(f"dev-{number}", f"atm-bh-{number}", branch=f"branch-{number}", layer=number)
                  for number in (1, 2))
    prs = (
        PullRequest(1, "url1", "sprint/bh-3", "branch-2", "sha3", CiState.PASS, ()),
        PullRequest(3, "url3", "branch-2", "branch-1", "sha2", CiState.PASS, ()),
        PullRequest(2, "url2", "branch-1", "base", "sha1", CiState.PASS, ()),
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

    historical_pr = PullRequest(99, "url", "pushed", "base", "sha", CiState.PASS, (), PrState.CLOSED)
    snapshot, _, _ = tick(replace(success, prs=Ok((historical_pr,))), STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]


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
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed", "github_unavailable"]
    assert snapshot.problems[0].first_seen == NOW
    assert actions == ()
    assert state.batch_due["solar"] == NOW + timedelta(minutes=10)

    snapshot, actions, state = tick(replace(first, gh=GhStatus("graphql", True, None,
                                                              NOW + timedelta(minutes=10), False)),
                                    state, NOW + timedelta(minutes=10), render)
    assert [problem.key for problem in snapshot.problems] == ["missing_pr:pushed"]
    assert [action.kind for action in actions] == ["batch"]
    assert state.batch_due["solar"] == NOW + timedelta(minutes=20)

    found_pr = PullRequest(1, "url", "pushed", "base", "sha", CiState.PASS, ())
    snapshot, actions, state = tick(replace(first, prs=Ok((found_pr,)),
                                            gh=GhStatus("graphql", True, None,
                                                        NOW + timedelta(minutes=20), False)),
                                    state, NOW + timedelta(minutes=20), render)
    assert snapshot.problems == ()
    assert actions == ()


def test_ci_and_conflict_problems_survive_until_their_sources_are_refreshed() -> None:
    value = phase(step("dev", "atm-bh-1", branch="pushed", assignee="solar"))
    failed_pr = PullRequest(1, "url", "pushed", "base", "sha", CiState.FAIL_BUG, ("lint",))
    first = inputs(value, prs=Ok((failed_pr,)), gh=GhStatus("graphql", True, None, NOW, False))
    snapshot, _, state = tick(first, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha"]
    unread = replace(first, prs=Err(NotFound("not_found", "prs", "retry")),
                     gh=GhStatus("graphql", False, None, NOW, False))
    snapshot, actions, state = tick(unread, state, NOW + timedelta(minutes=5), render)
    assert [problem.key for problem in snapshot.problems] == ["top_ci:sha", "github_unavailable"]
    assert actions == ()
    clean_pr = replace(failed_pr, ci=CiState.PASS, failed_checks=())
    snapshot, _, _ = tick(replace(first, prs=Ok((clean_pr,)),
                                  gh=GhStatus("graphql", True, None, NOW + timedelta(minutes=10), False)),
                          state, NOW + timedelta(minutes=10), render)
    assert snapshot.problems == ()

    conflict = Merge("pushed", "base", MergeState.CONFLICT, ("model.py",), (), NOW)
    with_merge = replace(first, prs=None, gh=GhStatus("off", None, None, None, False), merges=(Ok(conflict),))
    snapshot, _, state = tick(with_merge, STATE, NOW, render)
    assert [problem.key for problem in snapshot.problems] == ["conflict:pushed", "github_unavailable"]
    snapshot, actions, state = tick(replace(with_merge, merges=(Err(NotFound("not_found", "merge", "retry")),)),
                                    state, NOW + timedelta(minutes=5), render)
    assert [problem.key for problem in snapshot.problems] == ["conflict:pushed", "github_unavailable"]
    assert actions == ()
    clean_merge = replace(conflict, state=MergeState.CLEAN, files=())
    snapshot, _, _ = tick(replace(with_merge, merges=(Ok(clean_merge),)), state,
                          NOW + timedelta(minutes=10), render)
    assert [problem.key for problem in snapshot.problems] == ["github_unavailable"]


def test_shared_branch_reports_one_problem_per_kind_to_work_owner() -> None:
    dev = step("dev", "atm-bh-1", branch="shared", assignee="solar", status="closed")
    qa = step("qa", "atm-bh-1", kind="qa", branch="shared", assignee=None)
    qa_task = Task("qa", "quality-mgr", "assigned", 1, NOW, NOW, "review")
    conflict = Merge("shared", "base", MergeState.CONFLICT, ("model.py",), (), NOW)
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
    assert [view.state for view in snapshot.sprints] == [SprintState.CLOSED, SprintState.CLOSED, SprintState.IN_PROGRESS, SprintState.READY]
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
    conflict = Merge("topic", "base", MergeState.CONFLICT, ("model.py", "dispatch.py"), (), NOW)
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
    conflict = Merge("sprint/bh-1", "sprint/bh-1", MergeState.CONFLICT, ("model.py",), (), NOW)
    snapshot, actions, _ = tick(replace(common, merges=(Ok(conflict),)), STATE, NOW, render)
    assert [(action.kind, action.to) for action in actions] == [("send", "solar")]
    assert [problem.key for problem in snapshot.problems] == ["conflict:sprint/bh-1"]
    assert "model.py" in snapshot.problems[0].text

    error = NotFound("not_found", "checked_bead", "repair metadata")
    verified = replace(common, merges=(Ok(replace(conflict, state=MergeState.CLEAN, files=())),))
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
    verified = replace(supplied, merges=(Ok(Merge("topic", "base", MergeState.CLEAN, (), (), NOW)),))
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
