from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import MergeState, SprintState
from .ids import short_id
from .prs import active_pr
from .result import CommandFailed, Err, Error, NotFound, Timeout
from .types import (
    Action,
    DispatchContext,
    Merge,
    Phase,
    PullRequest,
    RemoteRef,
    RepoConfig,
    SectionRow,
    SpecFn,
    Sprint,
    Step,
    Task,
    Worktree,
)


@dataclass(frozen=True)
class DispatchOutcome:
    actions: tuple[Action, ...]
    held: tuple[SectionRow, ...]
    errors: tuple[Error, ...]


def verification_reason(error: Error) -> str:
    if isinstance(error, NotFound):
        detail = error.what
    elif isinstance(error, Timeout):
        detail = f"{' '.join(error.argv)} timed out after {error.seconds}s"
    elif isinstance(error, CommandFailed):
        detail = f"{' '.join(error.argv)} (exit {error.code}): {error.stderr.strip()}"
    else:
        detail = error.kind
    return f"{error.kind}: {detail}; {error.fix}"


def sprint_of(step: Step, phase: Phase, bead_prefix: str) -> Sprint | None:
    """The sprint a step belongs to: its sprint bead id, else its sprint tag as the id with or without the prefix."""
    by_id = {sprint.id: sprint for sprint in phase.sprints}
    if step.sprint_bead in by_id:
        return by_id[step.sprint_bead]
    by_tag = {short_id(sprint.id, bead_prefix): sprint for sprint in phase.sprints}
    return by_id.get(step.sprint or "") or by_tag.get(step.sprint or "")


def candidates(phase: Phase, tasks: tuple[Task, ...]) -> tuple[Step, ...]:
    tasked = {task.id for task in tasks if task.state in ("assigned", "active")}
    return tuple(
        step for step in phase.steps
        if step.kind in ("sanity", "qa") and step.status == SprintState.OPEN and not step.assignee
        and not step.anchor and not step.quick_fix and step.bead_id not in tasked
    )


def _gate_reasons(
    step: Step,
    target: str | None,
    worktrees: tuple[Worktree, ...],
    remote_by_branch: dict[str, str],
    merge_by_pair: dict[tuple[str, str], Merge],
    remote_error: Error | None,
) -> list[str]:
    """Why a ready step cannot dispatch yet. Only ready steps get these gates: a step that is not ready has no
    branch or worktree yet, so the gates would only repeat as `<missing>` rows beside its `not ready` row."""
    reasons: list[str] = []
    if remote_error is not None:
        reasons.append(f"origin verification unavailable: {verification_reason(remote_error)}")
    elif not step.branch or step.branch not in remote_by_branch:
        reasons.append(f"branch not on origin: {step.branch or '<missing>'}")
    if not step.worktree or not any(
        worktree.path == step.worktree and worktree.branch == step.branch for worktree in worktrees
    ):
        reasons.append(f"no worktree for {step.branch or '<missing>'}")
    merge = merge_by_pair.get((step.branch, target)) if step.branch and target else None
    if merge is not None and merge.state == MergeState.CONFLICT:
        reasons.append(f"merge conflict with {target}: {', '.join(merge.files)}")
    elif step.branch in remote_by_branch and remote_error is None:
        if target is None:
            reasons.append("merge verification unavailable: set the step or sprint PR target")
        elif merge is None:
            reasons.append(f"merge verification unavailable for {target}: no current result; retry the merge check")
        elif merge.state != MergeState.CLEAN:
            detail = "; ".join(merge.messages) or "retry the merge check"
            reasons.append(f"merge verification unavailable for {target}: {detail}")
    return reasons


def dispatch(
    phase: Phase,
    tasks: tuple[Task, ...],
    worktrees: tuple[Worktree, ...],
    remotes: tuple[RemoteRef, ...],
    merges: tuple[Merge, ...],
    prs: tuple[PullRequest, ...],
    repo: RepoConfig,
    lead: str,
    now: datetime,
    spec: SpecFn,
    remote_error: Error | None = None,
) -> DispatchOutcome:
    prs = tuple(pr for pr in prs if active_pr(pr))
    remote_by_branch = {remote.branch: remote.sha for remote in remotes}
    merge_by_pair = {(merge.branch, merge.target): merge for merge in merges}
    pr_by_branch = {pr.head: pr for pr in prs}
    actions: list[Action] = []
    held: list[SectionRow] = []
    errors: list[Error] = []

    for step in candidates(phase, tasks):
        sprint = sprint_of(step, phase, repo.bead_prefix)
        target = step.target or (sprint.target if sprint else None)
        reasons = ["not ready"] if not step.ready else _gate_reasons(
            step, target, worktrees, remote_by_branch, merge_by_pair, remote_error,
        )

        pr = pr_by_branch.get(step.branch or "")
        if reasons:
            age_s = max(0, int((now - (step.claimed_at or now)).total_seconds()))
            held.extend(SectionRow(step.bead_id, age_s, reason, pr.number if pr else None) for reason in reasons)
            continue
        if sprint is None:
            errors.append(NotFound("not_found", f"sprint for {step.bead_id}", "repair the phase step's sprint mapping"))
            continue
        if target is None:
            errors.append(NotFound("not_found", f"target for {step.bead_id}", "set the step or sprint PR target"))
            continue
        if step.worktree is None or step.branch is None:
            continue

        layer_prs = tuple(
            sorted(
                (candidate for candidate in prs if any(
                    sibling.branch == candidate.head and sibling.sprint == step.sprint for sibling in phase.steps
                )),
                key=lambda candidate: next(
                    (sibling.layer or 0 for sibling in phase.steps if sibling.branch == candidate.head), 0
                ),
            )
        )
        context = DispatchContext(
            step=step,
            sprint=sprint,
            phase_id=phase.root_id,
            phase_title=phase.title,
            lead=lead,
            commit=remote_by_branch[step.branch],
            base=pr.base if pr else target,
            worktree_path=step.worktree,
            pr=pr,
            layer_prs=layer_prs,
            repo=repo,
        )
        rendered = spec(context)
        if isinstance(rendered, Err):
            errors.append(rendered.error)
            continue
        recipient = repo.sanity_member if step.kind == "sanity" else repo.qa_member
        actions.append(Action(now, "assign", recipient, (step.bead_id,), f"dispatch {step.bead_id}", rendered.value))

    return DispatchOutcome(tuple(actions), tuple(held), tuple(errors))
