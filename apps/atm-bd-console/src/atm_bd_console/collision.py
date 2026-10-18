"""Detect two or more active tasks sharing a sprint or worktree, and name them in one collision row per sprint."""
from __future__ import annotations

import json

from .facts import task_sprint, task_step
from .types import Snapshot, Sprint


def collisions(snapshot: Snapshot) -> tuple[tuple[str, str, str], ...]:
    phase = snapshot.phase_data
    if phase is None:
        return ()
    steps = {step.bead_id: step for step in phase.steps}
    worktrees = {worktree.branch: worktree.path for worktree in snapshot.worktree_data if worktree.branch}
    mapped = []
    for task in snapshot.task_data:
        if task.state != "active":
            continue
        sprint = task_sprint(task, snapshot, steps)
        if sprint is None:
            continue
        step = task_step(task, snapshot, steps)
        path = (step.worktree if step else None) or worktrees.get(sprint.branch) or "unknown"
        mapped.append((task, sprint, path))
    groups: dict[str, tuple[Sprint, set[str], set[str]]] = {}
    for task, sprint, path in mapped:
        peers = [(other, other_sprint, other_path) for other, other_sprint, other_path in mapped
                 if other_sprint == sprint or (path != "unknown" and other_path == path)]
        if len(peers) < 2:
            continue
        _, names, paths = groups.setdefault(sprint.id, (sprint, set(), set()))
        names.update(other.agent for other, _, _ in peers)
        paths.update(other_path for _, _, other_path in peers)
    result = []
    for sprint_id, (_, names, paths) in sorted(groups.items()):
        agents = sorted(names)
        key = json.dumps([sprint_id, agents], separators=(",", ":"))
        message = f"collision: {sprint_id}; worktree {', '.join(sorted(paths))}; agents {', '.join(agents)}"
        result.append((key, sprint_id, message))
    return tuple(result)
