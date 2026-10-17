from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from rich.cells import cell_len

from .dispatch import candidates, dispatch, sprint_of, verification_reason
from .ids import short_id
from .result import Err, Error, Ok, Result
from .types import (
    Action,
    AgentView,
    Bead,
    Due,
    Inputs,
    Merge,
    Metrics,
    Phase,
    Problem,
    PrRow,
    PullRequest,
    Row,
    Section,
    Snapshot,
    SpecFn,
    SprintView,
    State,
    Step,
    StepView,
    Task,
)

CYCLE = timedelta(minutes=10)
IDLE_STOP = timedelta(minutes=60)


def _cycle_due(state: State, now: datetime) -> bool:
    return state.last_cycle is None or now - state.last_cycle >= CYCLE


def idle_stop_due(state: State, now: datetime) -> bool:
    """Whether persisted inactivity permits a read-free stopped tick."""
    return state.idle_since is not None and now - state.idle_since >= IDLE_STOP


def due(state: State, phase: Phase, bead_prefix: str, github_mode: str, now: datetime, tasks: tuple[Task, ...] = ()) -> Due:
    cycle_due = _cycle_due(state, now)
    ready = tuple(step for step in candidates(phase, tasks) if step.ready)
    pushed = {remote.branch for remote in state.remotes}
    fetch = cycle_due or bool(ready)
    pairs: list[tuple[str, str]] = []
    for step in phase.steps:
        target = _target(step, phase, bead_prefix)
        if step.branch and target and step.branch in pushed and cycle_due:
            pair = (step.branch, target)
            if pair not in pairs:
                pairs.append(pair)
    for step in ready:
        target = _target(step, phase, bead_prefix)
        if step.branch and target:
            pair = (step.branch, target)
            if pair not in pairs:
                pairs.append(pair)
    return Due(fetch, tuple(pairs), cycle_due and github_mode != "off")


def _target(step: Step, phase: Phase, bead_prefix: str) -> str | None:
    if step.target:
        return step.target
    sprint = sprint_of(step, phase, bead_prefix)
    return sprint.target if sprint else None


def tick(inputs: Inputs, state: State, now: datetime, spec: SpecFn) -> tuple[Snapshot, tuple[Action, ...], State]:
    errors = _read_errors(inputs)
    phase = inputs.phase.value if isinstance(inputs.phase, Ok) else None
    members = inputs.members.value if isinstance(inputs.members, Ok) else ()
    tasks = inputs.tasks.value if isinstance(inputs.tasks, Ok) else ()
    worktrees = inputs.worktrees.value if isinstance(inputs.worktrees, Ok) else ()
    remotes = inputs.remotes.value if isinstance(inputs.remotes, Ok) else state.remotes
    if isinstance(inputs.remotes, Err):
        remotes = ()
    merges = tuple(result.value for result in inputs.merges or () if isinstance(result, Ok))
    merge_errors = tuple(result.error for result in inputs.merges or () if isinstance(result, Err))
    if phase is not None and merge_errors:
        checked_pairs = {(merge.branch, merge.target) for merge in merges}
        unavailable = {
            (step.branch, target)
            for step in candidates(phase, tasks)
            if step.branch and (target := _target(step, phase, inputs.repo.bead_prefix))
            and (step.branch, target) not in checked_pairs
        }
        merges += tuple(Merge(branch, target, "error", (), tuple(
            verification_reason(error) for error in merge_errors
        ), now) for branch, target in sorted(unavailable))
    prs = inputs.prs.value if isinstance(inputs.prs, Ok) else ()
    if phase is not None:
        errors.extend(error for _, error in phase.step_errors)
        rejected = {bead_id for bead_id, _ in phase.step_errors}
        phase = replace(phase, steps=tuple(step for step in phase.steps if step.bead_id not in rejected))

    root_closed = phase.closed if phase else False
    idle_since = state.idle_since
    stopped: str | None = None
    if root_closed:
        stopped = "closed"
    elif phase is not None:
        active = any(step.ready or step.status == "in_progress" for step in phase.steps)
        idle_since = None if active else (idle_since or now)
        if idle_stop_due(replace(state, idle_since=idle_since), now):
            stopped = "idle"

    held = ()
    assigned: tuple[Action, ...] = ()
    if (phase is not None and stopped is None and isinstance(inputs.tasks, Ok)
            and isinstance(inputs.worktrees, Ok)):
        outcome = dispatch(
            phase, tasks, worktrees, remotes, merges, prs, inputs.repo, inputs.lead, now, spec,
            inputs.remotes.error if isinstance(inputs.remotes, Err) else None,
        )
        held = outcome.held
        assigned = outcome.actions
        errors.extend(outcome.errors)

    pr_rows, top_pr = _pr_rows(phase, prs, merges)
    top_ci = top_pr.ci if top_pr else None
    proposed = _problems(phase, held, merges, prs, top_pr, inputs, state, now) if stopped is None else ()
    problems, notices, batch_due, first_send, last_batch = _reconcile_problems(
        proposed, state, now, stopped is not None
    )
    actions = assigned + notices if stopped is None else ()
    sent = (state.sent + actions)[-5:]
    cycle_due = _cycle_due(state, now)
    new_state = replace(
        state,
        problems=problems,
        cycle_first_send=first_send,
        last_batch=last_batch,
        last_cycle=now if cycle_due else state.last_cycle,
        idle_since=idle_since,
        sent=sent,
        remotes=remotes,
        batch_due=batch_due,
    )
    sprint_views, mermaid = _sprint_views(phase, inputs.repo.bead_prefix)
    sections = (Section("Held", held),) if held else ()
    snapshot = Snapshot(
        schema=1,
        generated_at=now,
        phase=phase.label.removeprefix("phase-") if phase else "",
        root_closed=root_closed,
        stopped=stopped,
        agents=_agent_views(members, tasks, phase, inputs.task_beads, now),
        sections=sections,
        problems=problems,
        errors=tuple(errors),
        metrics=_metrics(phase, now),
        gh=inputs.gh,
        top_pr=top_pr.number if top_pr else None,
        top_ci=top_ci,
        mermaid=mermaid,
        sprints=sprint_views,
        interval_s=inputs.interval_s,
        lead=inputs.lead,
        bead_prefix=inputs.repo.bead_prefix,
        sent=sent,
        prs=pr_rows,
        files={},
    )
    return snapshot, actions, new_state


def _read_errors(inputs: Inputs) -> list[Error]:
    results = (inputs.phase, inputs.members, inputs.tasks, inputs.worktrees, inputs.remotes, inputs.prs,
               inputs.task_beads)
    errors = [result.error for result in results if isinstance(result, Err)]
    errors.extend(result.error for result in inputs.merges or () if isinstance(result, Err))
    if inputs.gh.error is not None and inputs.gh.error not in errors:
        errors.append(inputs.gh.error)
    return errors


def _agent_views(
    members, tasks: tuple[Task, ...], phase: Phase | None,
    task_beads: Result[tuple[Bead, ...]], now: datetime,
) -> tuple[AgentView, ...]:
    steps = {step.bead_id: step for step in phase.steps} if phase else {}
    root_id = phase.root_id if phase else None
    known_beads = {bead.id for bead in task_beads.value} if isinstance(task_beads, Ok) else None
    views: list[AgentView] = []
    for member in members:
        mine = sorted((task for task in tasks if task.agent == member.name), key=lambda task: (task.state != "active", task.position))
        if member.presence == "blocked":
            status = "blocked"
        elif member.presence in ("offline", "unknown") and mine:
            status = "offline"
        elif mine:
            status = "busy" if member.presence == "active" else "stalling"
        else:
            status = "idle"
        rows: list[Row] = []
        for task in mine:
            step = steps.get(task.id)
            if step is None:
                marker = None if known_beads is None else "red" if task.id in known_beads else "yellow"
            elif step.kind == "container":
                marker = None
            elif step.top_epic != root_id:
                marker = "red"
            else:
                marker = None
            kind = "other"
            if step and step.kind in ("dev", "fix"):
                kind = step.kind
            elif step and step.kind == "qa":
                kind = "fix_qa" if step.carry_forward else "sprint_qa"
            rows.append(Row(
                task_id=task.id,
                state="running" if task.state == "active" else "queued",
                kind=kind,
                difficulty=step.difficulty if step else None,
                tag=step.tag if step else "",
                marker=marker,
                age_s=max(0, int((now - task.state_since).total_seconds())),
                description=task.description,
            ))
        active = next((task for task in mine if task.state == "active"), None)
        views.append(AgentView(member.name, status, active.state_since if active else member.presence_since or now, len(mine), tuple(rows)))
    return tuple(views)


def _label_truncate(text: str, width: int) -> str:
    """Return text no wider than ``width`` cells, reserving room for an ellipsis."""
    if cell_len(text) <= width:
        return text
    kept = ""
    for character in text:
        if cell_len(kept + character + "…") > width:
            break
        kept += character
    return kept + "…"


def _label_title_lines(title: str, sprint_id: str, bead_prefix: str) -> tuple[str, ...]:
    """Make the two compact description lines for a Mermaid sprint node."""
    normalized = " ".join(title.split())
    prefix = f"{short_id(sprint_id, bead_prefix)}:"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix):].lstrip()
    words = normalized.split()
    lines: list[str] = []
    for word in words:
        candidate = f"{lines[-1]} {word}" if lines else word
        if not lines or cell_len(candidate) <= 28:
            if lines:
                lines[-1] = candidate
            else:
                lines.append(word)
            continue
        if len(lines) == 2:
            lines[-1] = _label_truncate(f"{lines[-1]} {word}", 28)
            break
        lines.append(word)
    return tuple(_label_truncate(line, 28) for line in lines[:2])


def _sprint_views(phase: Phase | None, bead_prefix: str) -> tuple[tuple[SprintView, ...], str]:
    if phase is None:
        return (), "graph TD"
    views: list[SprintView] = []
    lines = ["graph TD"]
    icons = {"closed": "✅", "in_progress": "🌀", "ready": "📥", "not_ready": "⏳"}
    for sprint in phase.sprints:
        steps = tuple(step for step in phase.steps if sprint_of(step, phase, bead_prefix) == sprint)
        state = "closed" if sprint.closed else (
            "in_progress" if any(step.status == "in_progress" for step in steps) else (
                "ready" if any(step.ready for step in steps) else "not_ready"
            )
        )
        views.append(SprintView(sprint.id, sprint.title, state, sprint.depends_on, tuple(
            StepView(step.bead_id, step.kind, step.status, step.assignee) for step in steps
        )))
        current = next((step for step in steps if step.status == "in_progress"), None)
        if current is None:
            current = next((step for step in steps if step.ready), None)
        label = f"{icons[state]} {short_id(sprint.id, bead_prefix)}"
        if current:
            label += f" {current.kind}:{current.assignee or 'unassigned'}"
        label = _label_truncate(label, 30)
        description = _label_title_lines(sprint.title, sprint.id, bead_prefix)
        if description:
            label += "<br/>" + "<br/>".join(description)
        node = sprint.id.replace("-", "_").replace(".", "_")
        lines.append(f'    {node}["{label.replace(chr(34), chr(39))}"]')
        for dependency in sprint.depends_on:
            source = dependency.replace("-", "_").replace(".", "_")
            lines.append(f"    {source} --> {node}")
    return tuple(views), "\n".join(lines)


def _metrics(phase: Phase | None, now: datetime) -> Metrics:
    if phase is None:
        return Metrics(0, 0, 0, 0)
    work = tuple(step for step in phase.steps if step.kind in ("dev", "fix"))
    done = sum(step.status == "closed" for step in work)
    closed = tuple(sprint.findings for sprint in phase.sprints if sprint.closed)
    average = round(sum(closed) / len(closed)) if closed else 0
    projected = sum(max(0, average - sprint.findings) for sprint in phase.sprints if not sprint.closed)
    total = len(work) + projected
    intervals = sorted(
        (step.claimed_at, step.closed_at or now) for step in phase.steps if step.claimed_at is not None
    )
    active_s = 0
    current_start: datetime | None = None
    current_end: datetime | None = None
    for start, end in intervals:
        if current_start is None:
            current_start, current_end = start, end
        elif current_end is not None and start <= current_end:
            current_end = max(current_end, end)
        else:
            active_s += max(0, int((current_end - current_start).total_seconds()))
            current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        active_s += max(0, int((current_end - current_start).total_seconds()))
    return Metrics(done, total, round(done * 100 / total) if total else 0, active_s)


def _pr_rows(
    phase: Phase | None, prs: tuple[PullRequest, ...], merges: tuple[Merge, ...]
) -> tuple[tuple[PrRow, ...], PullRequest | None]:
    if phase is None:
        return (), None
    by_branch = _branch_owners(phase)
    by_branch.update({sprint.branch: None for sprint in phase.sprints if sprint.branch not in by_branch})
    relevant = tuple(pr for pr in prs if pr.head in by_branch)
    bases = {pr.base for pr in relevant}
    top = next((pr for pr in relevant if pr.is_open and pr.head not in bases), None)
    merge_by_branch = {merge.branch: merge for merge in merges}
    rows = tuple(PrRow(
        pr=pr,
        layer=by_branch[pr.head].layer if by_branch[pr.head] else next(
            (sprint.layer for sprint in phase.sprints if sprint.branch == pr.head), None
        ),
        bead_id=by_branch[pr.head].bead_id if by_branch[pr.head] else next(
            (sprint.id for sprint in phase.sprints if sprint.branch == pr.head), None
        ),
        top=pr == top,
        merge=merge_by_branch.get(pr.head),
    ) for pr in relevant)
    return tuple(sorted(rows, key=lambda row: (row.layer if row.layer is not None else -1, row.pr.number))), top


def _branch_owners(phase: Phase) -> dict[str, Step]:
    owners: dict[str, Step] = {}
    ordered = sorted(phase.steps, key=lambda step: (
        step.kind not in ("dev", "fix"), step.assignee is None, step.status == "closed", step.bead_id,
    ))
    for step in ordered:
        if step.branch:
            owners.setdefault(step.branch, step)
    return owners


def _problems(
    phase: Phase | None,
    held,
    merges: tuple[Merge, ...],
    prs: tuple[PullRequest, ...],
    top_pr: PullRequest | None,
    inputs: Inputs,
    state: State,
    now: datetime,
) -> tuple[Problem, ...]:
    if phase is None:
        return tuple(
            problem for problem in state.problems
            if problem.key.startswith(("missing_pr:", "top_ci:", "conflict:"))
        )
    by_branch = _branch_owners(phase)
    by_id = {step.bead_id: step for step in phase.steps}
    conflicts = {
        f"conflict:{merge.branch}": Problem(
            f"conflict:{merge.branch}", step.bead_id, step.assignee or inputs.lead,
            f"merge conflict with {merge.target}: {', '.join(merge.files)}", now,
        )
        for merge in merges
        if merge.state == "conflict" and (step := by_branch.get(merge.branch)) is not None
    }
    problems: list[Problem] = []
    not_ready = {row.bead_id for row in held if row.reason == "not ready"}
    for row in held:
        if row.bead_id in not_ready:
            continue
        step = by_id[row.bead_id]
        if not step.branch:
            if not any(problem.key == f"hold:{step.bead_id}" for problem in problems):
                problems.append(Problem(
                    f"hold:{step.bead_id}", step.bead_id, step.assignee or inputs.lead,
                    "missing branch configuration", now,
                ))
            continue
        conflict = conflicts.get(f"conflict:{step.branch}")
        if conflict is not None and row.reason == conflict.text:
            continue
        problems.append(Problem(f"hold:{step.bead_id}", step.bead_id, step.assignee or inputs.lead, row.reason, now))
    problems.extend(conflicts.values())
    fresh_github = isinstance(inputs.prs, Ok) and inputs.gh.mode != "off" and inputs.gh.ok is True and (
        inputs.gh.at is not None and (state.last_cycle is None or inputs.gh.at > state.last_cycle)
    )
    if fresh_github:
        heads = {pr.head for pr in prs if pr.is_open}
        remotes = inputs.remotes.value if isinstance(inputs.remotes, Ok) else state.remotes
        pushed = {remote.branch for remote in remotes}
        open_branches = {
            step.branch for step in phase.steps
            if step.status != "closed" and step.kind in ("dev", "fix", "sanity", "qa")
        }
        for branch, step in by_branch.items():
            if branch in open_branches and branch in pushed and branch not in heads:
                problems.append(Problem(
                    f"missing_pr:{branch}", step.bead_id, step.assignee or inputs.lead,
                    f"pushed branch {branch} has no open PR", now,
                ))
    if fresh_github and top_pr is not None and top_pr.ci == "failure":
        checks = ", ".join(top_pr.failed_checks)
        problems.append(Problem(
            f"top_ci:{top_pr.head_sha}", by_branch.get(top_pr.head).bead_id if by_branch.get(top_pr.head) else None,
            inputs.lead, f"top PR #{top_pr.number} CI failed: {checks}", now,
        ))
    current_keys = {problem.key for problem in problems}
    refreshed_merges = {merge.branch for merge in merges if merge.state != "error"}
    for previous in state.problems:
        if previous.key in current_keys:
            continue
        if (
            previous.key.startswith(("missing_pr:", "top_ci:")) and not fresh_github
            or previous.key.startswith("conflict:") and previous.key.removeprefix("conflict:") not in refreshed_merges
        ):
            problems.append(previous)
    return tuple(problems)


def _reconcile_problems(
    proposed: tuple[Problem, ...], state: State, now: datetime, stopped: bool
) -> tuple[tuple[Problem, ...], tuple[Action, ...], dict[str, datetime], datetime | None, datetime | None]:
    if stopped:
        return (), (), {}, state.cycle_first_send, state.last_batch
    previous = {problem.key: problem for problem in state.problems}
    problems = tuple(
        replace(problem, first_seen=previous[problem.key].first_seen)
        if problem.key in previous and previous[problem.key].to == problem.to else problem
        for problem in proposed
    )
    new = tuple(problem for problem in problems if problem.key not in previous or previous[problem.key].to != problem.to)
    actions = [Action(now, "send", problem.to, (problem.bead_id,) if problem.bead_id else (), problem.text, None) for problem in new]
    by_recipient: dict[str, list[Problem]] = {}
    for problem in problems:
        by_recipient.setdefault(problem.to, []).append(problem)
    batch_due = {recipient: due_at for recipient, due_at in state.batch_due.items() if recipient in by_recipient}
    for problem in new:
        batch_due.setdefault(problem.to, now + CYCLE)
    last_batch = state.last_batch
    for recipient, due_at in tuple(batch_due.items()):
        if now >= due_at:
            current = by_recipient[recipient]
            bead_ids = tuple(problem.bead_id for problem in current if problem.bead_id)
            text = "\n".join(problem.text for problem in current)
            actions.append(Action(now, "batch", recipient, bead_ids, text, None))
            while due_at <= now:
                due_at += CYCLE
            batch_due[recipient] = due_at
            last_batch = now
    first_send = (state.cycle_first_send or min(problem.first_seen for problem in problems)) if problems else None
    return problems, tuple(actions), batch_due, first_send, last_batch
