"""Detect active tasks sharing a known task-owned worktree."""
from __future__ import annotations

import json

from .facts import task_sprint
from .types import Snapshot


def collisions(snapshot: Snapshot) -> tuple[tuple[str, str, str], ...]:
    phase = snapshot.phase_data
    if phase is None:
        return ()
    steps = {step.bead_id: step for step in phase.steps}
    beads = {bead.id: bead for bead in snapshot.task_bead_data}
    worktrees = {worktree.branch: worktree.path for worktree in snapshot.worktree_data if worktree.branch}
    groups: dict[str, list[tuple[str, str]]] = {}
    for task in snapshot.task_data:
        if task.state != "active":
            continue
        sprint = task_sprint(task, snapshot, steps)
        if sprint is None:
            continue
        step = steps.get(task.id)
        bead = beads.get(task.id)
        metadata = bead.metadata if bead else {}
        path = (step.worktree if step else None) or metadata.get("worktree")
        branch = (step.branch if step else None) or metadata.get("branch")
        if not path and isinstance(branch, str):
            path = worktrees.get(branch)
        if not isinstance(path, str) or not path.strip() or path == "unknown":
            continue
        groups.setdefault(path, []).append((sprint.id, task.agent))
    result = []
    for path, peers in sorted(groups.items()):
        if len(peers) < 2:
            continue
        sprint_ids = sorted({sprint_id for sprint_id, _ in peers})
        sprint_id = sprint_ids[0]
        agents = sorted({agent for _, agent in peers})
        key = json.dumps([sprint_id, agents, path], separators=(",", ":"))
        message = f"collision: {', '.join(sprint_ids)}; worktree {path}; agents {', '.join(agents)}"
        result.append((key, sprint_id, message))
    return tuple(result)
