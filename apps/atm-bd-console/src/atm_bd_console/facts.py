from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from .dispatch import sprint_of
from .enums import AgentIcon, CiState, MergeState, PrState, SprintState
from .types import AgentView, Member, PrRow, Snapshot, Sprint, Step, Task


def agent_icon_of(member: Member | AgentView | None, step_kind: str) -> AgentIcon:
    if step_kind == "sanity":
        return AgentIcon.SEVERITY
    if step_kind in ("qa", "sprint_qa"):
        return AgentIcon.QA_RUNNING
    if member is None:
        return AgentIcon.OTHER
    harness = (member.harness or "").lower()
    model = (member.model or "").lower()
    if harness == "claude-code":
        choices = (AgentIcon.HAIKU, AgentIcon.SONNET, AgentIcon.OPUS, AgentIcon.FABLE)
        fallback = AgentIcon.SONNET
    elif harness == "codex-cli":
        choices = (AgentIcon.ASTRA, AgentIcon.TERRA, AgentIcon.LUNA, AgentIcon.SOL)
        fallback = AgentIcon.TERRA
    else:
        return AgentIcon.OTHER
    return next((choice for choice in choices if choice.value in model), fallback)


@dataclass(frozen=True)
class SprintFacts:
    sprint: Sprint
    status: SprintState
    agents: tuple[AgentIcon, ...]
    qa_result: AgentIcon | None
    ci: CiState
    pr_state: PrState
    pr: PrRow | None


def _task_step(task: Task, snapshot: Snapshot, steps: dict[str, Step]) -> Step | None:
    beads = {bead.id: bead for bead in snapshot.task_bead_data}
    current = task.id
    visited: set[str] = set()
    while current not in visited:
        visited.add(current)
        if current in steps:
            return steps[current]
        bead = beads.get(current)
        if bead is None or bead.parent is None:
            return None
        current = bead.parent
    return None


def _task_sprint(task: Task, snapshot: Snapshot, steps: dict[str, Step]) -> Sprint | None:
    phase = snapshot.phase_data
    if phase is None:
        return None
    step = _task_step(task, snapshot, steps)
    if step is not None:
        found = sprint_of(step, phase, snapshot.bead_prefix)
        if found is not None:
            return found
    beads = {bead.id: bead for bead in snapshot.task_bead_data}
    sprints = {sprint.id: sprint for sprint in phase.sprints}
    current = task.id
    visited: set[str] = set()
    while current not in visited:
        visited.add(current)
        if current in sprints:
            return sprints[current]
        bead = beads.get(current)
        if bead is None or bead.parent is None:
            return None
        current = bead.parent
    return None


def _qa_result(steps: tuple[Step, ...]) -> AgentIcon | None:
    from .model import qa_state_from_close

    closed_qa = tuple(step for step in steps if step.kind in ("qa", "sprint_qa") and not step.carry_forward and step.closed_at)
    if not closed_qa:
        return None
    newest = max(closed_qa, key=lambda step: step.closed_at or datetime.min)
    if any(step.kind in ("dev", "fix") and step.closed_at and newest.closed_at
           and step.closed_at > newest.closed_at for step in steps):
        return None
    verdict = qa_state_from_close(newest.close_reason)
    return {"pass": AgentIcon.QA_PASS, "fail": AgentIcon.QA_FAIL}.get(verdict or "")


def _selected_pr(sprint: Sprint, steps: tuple[Step, ...], snapshot: Snapshot) -> PrRow | None:
    branches = {sprint.branch, *(step.branch for step in steps if step.branch)}
    candidates = [row for row in snapshot.prs if row.pr.head in branches]
    priority = {PrState.OPEN: 2, PrState.DRAFT: 2, PrState.MERGED: 1, PrState.CLOSED: 0}
    return max(candidates, key=lambda row: (priority.get(row.pr.state, -1), row.pr.number), default=None)


def sprint_facts(snapshot: Snapshot) -> tuple[SprintFacts, ...]:
    phase = snapshot.phase_data
    if phase is None:
        return ()
    by_step = {step.bead_id: step for step in phase.steps}
    by_member = {member.name: member for member in snapshot.member_data}
    active = sorted((task for task in snapshot.task_data if task.state == "active"),
                    key=lambda task: (task.state_since, task.agent))
    result: list[SprintFacts] = []
    for sprint in phase.sprints:
        steps = tuple(step for step in phase.steps if sprint_of(step, phase, snapshot.bead_prefix) == sprint)
        agents = tuple(agent_icon_of(by_member.get(task.agent), step.kind if step else "other")
                       for task in active if _task_sprint(task, snapshot, by_step) == sprint
                       for step in (_task_step(task, snapshot, by_step),))
        selected = _selected_pr(sprint, steps, snapshot)
        available = snapshot.gh.mode != "off" and snapshot.gh.ok is True and not snapshot.gh.truncated
        if not available:
            ci, pr_state = CiState.UNKNOWN, PrState.UNKNOWN
        elif selected is None:
            ci, pr_state = CiState.NONE, PrState.NONE
        else:
            pr = selected.pr
            ci = pr.ci
            pr_state = pr.state
            if (pr_state in (PrState.OPEN, PrState.DRAFT) and selected.merge is not None
                    and selected.merge.branch == pr.head and selected.merge.target == pr.base
                    and selected.merge.state == MergeState.CONFLICT):
                pr_state = PrState.CONFLICT
        view = next((view for view in snapshot.sprints if view.id == sprint.id), None)
        state = sprint.status or (view.state if view else SprintState.UNKNOWN)
        result.append(SprintFacts(sprint, state, agents, _qa_result(steps), ci, pr_state, selected))
    return tuple(result)


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
        sprint = _task_sprint(task, snapshot, steps)
        if sprint is None:
            continue
        step = _task_step(task, snapshot, steps)
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
