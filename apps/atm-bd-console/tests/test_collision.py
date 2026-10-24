from __future__ import annotations

from dataclasses import replace

from contract_samples import BEAD, PHASE, SNAPSHOT, SPRINT, STEP, TASK

from atm_bd_console import collision, facts
from atm_bd_console.types import Phase, Snapshot, Worktree


def phase(snapshot: Snapshot) -> Phase:
    assert snapshot.phase_data is not None
    return snapshot.phase_data


def active_snapshot(*paths: str | None):
    steps = tuple(replace(STEP, bead_id=f"{SPRINT.id}.fix-{index}", kind="fix", worktree=path,
                          branch=f"fix/test-{index}", sprint_bead=SPRINT.id)
                  for index, path in enumerate(paths))
    tasks = tuple(replace(TASK, id=step.bead_id, agent=f"agent-{index}", state="active")
                  for index, step in enumerate(steps))
    return replace(SNAPSHOT, phase_data=replace(PHASE, sprints=(SPRINT,), steps=steps), task_data=tasks,
                   task_bead_data=(), worktree_data=(Worktree("/sprint-worktree", SPRINT.branch, "sha"),))


def test_same_sprint_separate_worktrees_do_not_collide() -> None:
    assert collision.collisions(active_snapshot("/fix/first", "/fix/second")) == ()


def test_same_worktree_reports_one_collision_naming_both_agents() -> None:
    rows = collision.collisions(active_snapshot("/fix/shared", "/fix/shared"))
    assert len(rows) == 1
    assert rows[0][1] == SPRINT.id
    assert rows[0][2] == f"collision: {SPRINT.id}; worktree /fix/shared; agents agent-0, agent-1"


def test_unknown_paths_never_use_the_sprint_worktree() -> None:
    assert collision.collisions(active_snapshot(None, None, "unknown", "")) == ()


def test_separate_shared_paths_produce_separate_notices() -> None:
    rows = collision.collisions(active_snapshot("/fix/first", "/fix/first", "/fix/second", "/fix/second"))
    assert len(rows) == 2
    assert "agents agent-0, agent-1" in rows[0][2]
    assert "agents agent-2, agent-3" in rows[1][2]
    assert rows[0][0] != rows[1][0]


def test_same_worktree_across_sprints_is_one_collision() -> None:
    snapshot = active_snapshot("/fix/shared", "/fix/shared")
    other_sprint = replace(SPRINT, id="atm-bh-7", branch="sprint/bh-7")
    first, second = phase(snapshot).steps
    second = replace(second, sprint="bh-7", sprint_bead=other_sprint.id)
    snapshot = replace(snapshot, phase_data=replace(phase(snapshot), sprints=(SPRINT, other_sprint),
                                                    steps=(first, second)))
    rows = collision.collisions(snapshot)
    assert len(rows) == 1
    assert "atm-bh-6, atm-bh-7" in rows[0][2]
    assert "agents agent-0, agent-1" in rows[0][2]


def test_task_branch_resolves_its_own_worktree() -> None:
    snapshot = active_snapshot(None, None)
    worktrees = tuple(Worktree("/fix/shared", step.branch, "sha") for step in phase(snapshot).steps)
    assert len(collision.collisions(replace(snapshot, worktree_data=worktrees))) == 1


def test_task_bead_worktree_or_branch_resolves_without_a_step() -> None:
    snapshot = active_snapshot(None, None)
    beads = tuple(replace(BEAD, id=task.id, parent=SPRINT.id, metadata=metadata)
                  for task, metadata in zip(snapshot.task_data, (
                      {"worktree": "/fix/shared"}, {"branch": "fix/second"},
                  ), strict=True))
    snapshot = replace(snapshot, phase_data=replace(phase(snapshot), steps=()), task_bead_data=beads,
                       worktree_data=(Worktree("/fix/shared", "fix/second", "sha"),))
    assert len(collision.collisions(snapshot)) == 1


def test_task_does_not_inherit_ancestor_steps_worktree() -> None:
    snapshot = active_snapshot(None, None)
    parent = replace(STEP, bead_id=SPRINT.id, worktree="/ancestor", sprint_bead=SPRINT.id)
    beads = tuple(replace(BEAD, id=task.id, parent=SPRINT.id, metadata={}) for task in snapshot.task_data)
    snapshot = replace(snapshot, phase_data=replace(phase(snapshot), steps=(parent,)), task_bead_data=beads)
    assert collision.collisions(snapshot) == ()


def test_collisions_is_owned_by_the_collision_module() -> None:
    # The sprint design names collision.py as the owner of detection; facts.py keeps only sprint facts.
    assert collision.collisions.__module__ == "atm_bd_console.collision"
    assert not hasattr(facts, "collisions")
