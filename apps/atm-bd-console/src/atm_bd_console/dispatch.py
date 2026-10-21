from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import MergeState, SprintState
from .error_text import error_text
from .ids import short_id
from .prs import active_pr
from .result import Err, Error, NotFound
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


verification_reason = error_text


def sprint_of(step: Step, phase: Phase, bead_prefix: str) -> Sprint | None:
    """The sprint a step belongs to: its sprint bead id, else its sprint tag as the id with or without the prefix."""
    by_id = {sprint.id: sprint for sprint in phase.sprints}
    if step.sprint_bead in by_id:
        return by_id[step.sprint_bead]
    by_tag = {short_id(sprint.id, bead_prefix): sprint for sprint in phase.sprints}
    return by_id.get(step.sprint or "") or by_tag.get(step.sprint or "")


def candidates(phase: Phase, tasks: tuple[Task, ...], closed: frozenset[str]) -> tuple[Step, ...]:
    """Open sanity and QA steps with no ATM task of the same id in any state, so none is dispatched twice.

    `tasks` are the live (assigned or active) tasks; `closed` are the ids whose task history ends closed, read only
    for the steps that would otherwise be candidates. The one exception is a closed task from an earlier sanity
    round (ANC-057): a sanity FAIL below the round cap closes its task and reopens the sanity bead with its
    assignee cleared, so that bead is dispatched again."""
    live = {task.id for task in tasks if task.state in ("assigned", "active")}
    return tuple(
        step for step in phase.steps
        if step.kind in ("sanity", "qa") and step.status == SprintState.OPEN
        and not step.anchor and not step.quick_fix and step.bead_id not in live
        and (step.bead_id not in closed or (step.kind == "sanity" and not step.assignee))
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
        where = step.worktree or "<worktree>"
        reasons.append(f"branch not on origin: {step.branch or '<missing>'}"
                       + (f"; push it: `git -C {where} push -u origin {step.branch}`" if step.branch else ""))
    if not step.worktree or not any(
        worktree.path == step.worktree and worktree.branch == step.branch for worktree in worktrees
    ):
        reasons.append(f"no worktree for {step.branch or '<missing>'} at {step.worktree or '<unset>'}; "
                       "create it with the sc-git-worktree skill (not `git worktree add`)")
    merge = merge_by_pair.get((step.branch, target)) if step.branch and target else None
    if merge is not None and merge.state == MergeState.CONFLICT:
        reasons.append(f"merge conflict with {target}: {', '.join(merge.files)}")
    elif step.branch in remote_by_branch and remote_error is None:
        if target is None:
            reasons.append("merge verification unavailable: set the step or sprint PR target")
        elif merge is None:
            reasons.append(f"merge verification unavailable for {target}: no current result; retry the merge check")
        elif merge.state == MergeState.BASE_DELETED:
            reasons.append(f"base branch {target} is not on origin: push it (`git push -u origin {target}`) "
                           "or set the step or sprint PR target to a branch that exists")
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
    *,
    roster: frozenset[str],
    closed: frozenset[str],
) -> DispatchOutcome:
    prs = tuple(pr for pr in prs if active_pr(pr))
    remote_by_branch = {remote.branch: remote.sha for remote in remotes}
    merge_by_pair = {(merge.branch, merge.target): merge for merge in merges}
    pr_by_branch = {pr.head: pr for pr in prs}
    actions: list[Action] = []
    held: list[SectionRow] = []
    errors: list[Error] = []

    for step in candidates(phase, tasks, closed):
        sprint = sprint_of(step, phase, repo.bead_prefix)
        target = step.target or (sprint.target if sprint else None)
        reasons = (["not ready"] if not step.ready else _gate_reasons(
                       step, target, worktrees, remote_by_branch, merge_by_pair, remote_error,
                   ))

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
        role_member = repo.sanity_member if step.kind == "sanity" else repo.qa_member
        recipient = step.assignee if step.assignee in roster else role_member
        actions.append(Action(now, "assign", recipient, (step.bead_id,), f"dispatch {step.bead_id}", rendered.value))

    return DispatchOutcome(tuple(actions), tuple(held), tuple(errors))
