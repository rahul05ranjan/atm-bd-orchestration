from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .agent_roles import agent_group
from .dispatch import sprint_of
from .enums import AgentIcon, CiState, MergeState, PrState, QaState, SprintState
from .ids import short_id
from .pr_selection import select_pr
from .types import AgentView, Member, PrRow, Snapshot, Sprint, Step, Task

# ANC-024: the roster model string, lower-cased, is matched by contains; the first hit wins. SOL comes last
# because "sol" is inside "resolve", so a model such as GPT-5.6-Terra-Resolve must take TERRA first.
_MODEL_ICONS = (AgentIcon.ASTRA, AgentIcon.TERRA, AgentIcon.LUNA, AgentIcon.SOL,
                AgentIcon.HAIKU, AgentIcon.SONNET, AgentIcon.OPUS, AgentIcon.FABLE)


_QA_ICONS = {QaState.RUNNING: AgentIcon.QA_RUNNING, QaState.PASS: AgentIcon.QA_PASS, QaState.FAIL: AgentIcon.QA_FAIL}


def agent_icon_of(member: Member | AgentView | None, step_kind: str) -> AgentIcon:
    """Select agent icons centrally, including gavels for fix verification.

    ``qa`` is the stored Step kind and roster role; ``sprint_qa`` is a
    display-row role. Both use the ANC-017 gavel, with the member's QA state
    when present and running otherwise; icons.icon_for only looks it up.
    """
    group = agent_group(step_kind)
    if group == "sanity":
        return AgentIcon.SANITY
    if group == "qa":
        qa_state = member.qa_state if isinstance(member, AgentView) else None
        return _QA_ICONS[qa_state] if qa_state is not None else AgentIcon.QA_RUNNING
    if member is None:
        return AgentIcon.OTHER
    model = (member.model or "").lower()
    named = next((icon for icon in _MODEL_ICONS if icon.value in model), None)
    if named is not None:
        return named
    # ANC-025: an unmatched model falls back by harness; any other harness is OTHER.
    harness = (member.harness or "").lower()
    if harness == "claude-code":
        return AgentIcon.SONNET
    if harness == "codex-cli":
        return AgentIcon.TERRA
    return AgentIcon.OTHER


@dataclass(frozen=True)
class ActiveAgent:
    name: str
    icon: AgentIcon
    since: datetime


@dataclass(frozen=True)
class PrFacts:
    state: PrState
    row: PrRow | None


@dataclass(frozen=True)
class CiFacts:
    status: CiState


@dataclass(frozen=True)
class SprintFacts:
    id: str
    slug: str
    status: SprintState
    active: tuple[ActiveAgent, ...]
    gavel: AgentIcon | None
    pr: PrFacts | None
    ci: CiFacts | None
    problems: tuple[str, ...]


def task_step(task: Task, snapshot: Snapshot, steps: dict[str, Step]) -> Step | None:
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


def task_sprint(task: Task, snapshot: Snapshot, steps: dict[str, Step]) -> Sprint | None:
    phase = snapshot.phase_data
    if phase is None:
        return None
    step = task_step(task, snapshot, steps)
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


def _gavel(steps: tuple[Step, ...]) -> AgentIcon | None:
    """The sprint gavel once its dev step is closed: red while any blocking child is open, green when none is.

    Blocking children are open fix steps of poured blocking groups and open blocking finding steps; a minor or
    important finding does not block the sprint. A stored QA verdict is not read.
    """
    if not any(step.kind == "dev" and step.closed_at for step in steps):
        return None
    blocking_open = any(
        step.kind in ("fix", "finding") and step.severity == "blocking" and step.status != SprintState.CLOSED
        for step in steps
    )
    return AgentIcon.QA_FAIL if blocking_open else AgentIcon.QA_PASS


def _selected_pr(sprint: Sprint, steps: tuple[Step, ...], snapshot: Snapshot) -> PrRow | None:
    branches = {sprint.branch, *(step.branch for step in steps if step.branch)}
    candidates = [row for row in snapshot.prs if row.pr.head in branches]
    selected = select_pr(row.pr for row in candidates)
    return next((row for row in candidates if row.pr is selected), None)


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
        agents = tuple(ActiveAgent(task.agent, agent_icon_of(by_member.get(task.agent), step.kind if step else "other"),
                                   task.state_since)
                       for task in active if task_sprint(task, snapshot, by_step) == sprint
                       for step in (task_step(task, snapshot, by_step),))
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
        problems = []
        if state == SprintState.UNKNOWN:
            problems.append(f"unknown sprint state {sprint.raw_status!r} for {sprint.id}")
        if not available:
            cause = "off" if snapshot.gh.mode == "off" else "truncated" if snapshot.gh.truncated else (
                snapshot.gh.error.kind if snapshot.gh.error else "unavailable"
            )
            problems.append(f"GitHub data {cause}")
        elif selected is not None:
            if ci == CiState.UNKNOWN:
                problems.append(f"unknown CI state {selected.pr.raw_ci!r} for #{selected.pr.number}")
            if pr_state == PrState.UNKNOWN:
                problems.append(f"unknown PR state {selected.pr.raw_state!r} for #{selected.pr.number}")
            merge = selected.merge
            if merge is not None and merge.state == MergeState.ERROR and merge.branch == selected.pr.head and merge.target == selected.pr.base:
                problems.append(f"merge check failed for {merge.branch} against {merge.target}")
        pr_facts = None if pr_state == PrState.NONE else PrFacts(pr_state, selected)
        result.append(SprintFacts(sprint.id, short_id(sprint.id, snapshot.bead_prefix), state, agents,
                                  _gavel(steps), pr_facts, CiFacts(ci), tuple(problems)))
    return tuple(result)
