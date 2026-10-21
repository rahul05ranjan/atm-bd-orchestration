"""Impure tick boundary; scheduling and decisions remain in :mod:`model`."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal

from . import atm_bd, atm_source, beads_source, git_source, github_source, ledger_source, model
from .dispatch import candidates, sprint_of, verification_reason
from .enums import MergeState, PrState, SprintState
from .log import TickLog, create_console_logger, error_message
from .result import Err, NotFound, Ok, Result
from .state import StateStore, load_merges, load_prs, load_snapshot
from .types import (
    Action,
    ActionLine,
    Config,
    GhStatus,
    Inputs,
    Merge,
    MergeFile,
    Metrics,
    Phase,
    PrsFile,
    Snapshot,
    State,
    StepResult,
    Task,
)


def _gh(mode: Literal["graphql", "rest", "off"]) -> GhStatus:
    return GhStatus(mode, None, None, None, False)


def _run_actions(config: Config, actions: tuple[Action, ...], store: StateStore, now: datetime, logger: TickLog) -> tuple[tuple[Action, ...], tuple]:
    """Run actions and retain their actual delivery outcome for reporting."""
    errors = []
    reported = []
    for action in actions:
        bead = action.bead_ids[0] if action.bead_ids else None
        result = Ok(None)
        if config.dry_run:
            logged = store.append_action(ActionLine(now, "dry_run", action.to, bead, action.text))
            if isinstance(logged, Err): errors.append(logged.error)
            reported.append(replace(action, outcome="dry_run"))
        else:
            if action.kind == "assign" and action.spec is not None and bead:
                result = atm_source.assign(action.to, bead, action.spec, config.lead, config.team)
            else:
                sender, sender_team = config.notification_sender.split("@", 1)
                recipient = action.to if "@" in action.to else f"{action.to}@{config.team}"
                result = atm_source.send(recipient, action.text, sender, sender_team)
            logged = store.append_action(ActionLine(now, action.kind, action.to, bead, action.text))
            if isinstance(logged, Err): errors.append(logged.error)
            if isinstance(result, Err):
                errors.append(result.error)
                held = store.append_action(ActionLine(now, "hold", action.to, bead, result.error.fix))
                if isinstance(held, Err): errors.append(held.error)
                reported.append(replace(action, outcome=f"failed {result.error.kind}"))
            else:
                reported.append(replace(action, outcome="sent"))
        if isinstance(result, Err):
            logger.mark_reported(result.error)
        logger.event(
            "action", effect=action.kind, recipient=action.to, task_ids=list(action.bead_ids),
            outcome=reported[-1].outcome,
            level="error" if isinstance(result, Err) else "info",
            message=error_message(asdict(result.error)) if isinstance(result, Err) else None,
            error=asdict(result.error) if isinstance(result, Err) else None,
        )
    return tuple(reported), tuple(errors)


def _durable_state(state: State, proposed: State, actions: tuple[Action, ...], reported: tuple[Action, ...]) -> State:
    """Commit only effects whose boundary call actually succeeded.

    A failed batch keeps its previous due time (or none, so the next tick sends
    it again), so a recipient's problems are never marked delivered unsent.
    """
    successful = tuple(action for action in reported if action.outcome == "sent")
    failed = tuple(action for action in reported if action.outcome and action.outcome.startswith("failed "))
    failed_batches = {action.to for action in failed if action.kind == "batch"}
    batch_due = dict(proposed.batch_due)
    for recipient in failed_batches:
        if recipient in state.batch_due:
            batch_due[recipient] = state.batch_due[recipient]
        else:
            batch_due.pop(recipient, None)
    notices_succeeded = any(action.kind == "batch" for action in successful)
    return replace(
        proposed,
        cycle_first_send=proposed.cycle_first_send if notices_succeeded else state.cycle_first_send,
        last_batch=proposed.last_batch if any(action.kind == "batch" for action in successful) else state.last_batch,
        sent=(*state.sent, *successful)[-5:],
        batch_due=batch_due,
    )


def _write_snapshot(store: StateStore, snapshot: Snapshot, logger: TickLog) -> Snapshot:
    written = store.write_snapshot(snapshot)
    if isinstance(written, Ok):
        return snapshot
    previous_errors = len(logger.errors)
    logger.error(written.error, source="snapshot.write")
    logger.flush()
    return replace(snapshot, errors=(*snapshot.errors, written.error, *logger.errors[previous_errors:]))


def _branch_groups(phase, repo) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(dict.fromkeys((sprint.branch, *(
        step.branch for step in phase.steps if step.branch and sprint_of(step, phase, repo.bead_prefix) == sprint
    )))) for sprint in phase.sprints)


def _refresh_github(config: Config, now: datetime, gh: GhStatus, groups=()):
    if config.github == "off":
        return Ok(()), _gh("off")
    code_root = config.code_root or config.repo_root
    budget = github_source.Budget(config.github, code_root, config.github_quota_threshold,
                                  config.github_poll_interval_s, now, gh, config.github_repo)
    result, status = github_source.open_prs(config.github, code_root, groups, budget, config.github_repo)
    if status.paused and gh.mode == config.github:
        status = replace(status, ok=gh.ok, error=None, at=gh.at, truncated=gh.truncated)
    return result, status


def _stopped_tick(config: Config, state: State, now: datetime, store: StateStore, file, repo, root_bead, logger,
                  force_github: bool = False):
    shown_ids = atm_bd.closed_show_ids(file, ())
    shown = logger.result("beads.show_closed", beads_source.show(shown_ids), task_ids=list(shown_ids))
    phase = shown if isinstance(shown, Err) else atm_bd.phase(file, root_bead, (), (), shown.value)
    if root_bead is not None and root_bead.status == SprintState.CLOSED and isinstance(phase, Ok):
        phase = Ok(replace(phase.value, step_errors=()))
    cached_prs = load_prs(config.repo_root, file.root_id)
    cached_merges = load_merges(config.repo_root, file.root_id)
    cached_snapshot = load_snapshot(config.repo_root, file.root_id)
    history = store.load_actions()
    prs = Ok(cached_prs.value.prs) if isinstance(cached_prs, Ok) else Ok(())
    gh = cached_prs.value.gh if config.github != "off" and isinstance(cached_prs, Ok) else _gh(config.github)
    gh = replace(gh, poll_interval_s=config.github_poll_interval_s, quota_threshold=config.github_quota_threshold)
    cache_errors = ()
    if force_github and config.github != "off":
        groups = _branch_groups(phase.value, repo) if config.github == "rest" and isinstance(phase, Ok) else ()
        refreshed, gh = _refresh_github(config, now, gh, groups)
        if isinstance(refreshed, Ok):
            prs = refreshed
        previous_read = cached_prs.value.read_at if isinstance(cached_prs, Ok) else now
        saved = store.write_prs(PrsFile(now if isinstance(refreshed, Ok) else previous_read, gh, prs.value, attempted_at=now))
        if isinstance(saved, Err):
            cache_errors = (saved.error,)
            logger.event("error", source="prs.write", error=asdict(saved.error))
    merges = tuple(Ok(merge) for merge in cached_merges.value.merges.values()) if isinstance(cached_merges, Ok) else ()
    sent = tuple(
        Action(line.at, line.kind if line.kind in ("assign", "send", "batch") else "send", line.to or "—",
               (line.bead,) if line.bead else (),
               line.text if line.kind in ("assign", "send", "batch") else f"{line.kind}: {line.text}", None, "recorded")
        for line in history.value[-5:]
    ) if isinstance(history, Ok) else ()
    snapshot, _, next_state = model.tick(
        Inputs(now, config.lead, repo, config.interval_s, phase, Ok(()), Ok(()), Ok(()), None, merges, prs, gh, Ok(()), Ok(())),
        state,
        now,
        partial(atm_bd.dispatch_spec, templates=file.templates),
    )
    if isinstance(cached_snapshot, Ok):
        snapshot = replace(snapshot, agents=cached_snapshot.value.agents)
    snapshot = replace(snapshot, sent=sent)
    next_state = state if config.dry_run else next_state
    written = store.write_state(next_state)
    history_errors = () if isinstance(history, Ok) or isinstance(history.error, NotFound) else (history.error,)
    errors = (*snapshot.errors, *history_errors, *store.load_errors, *cache_errors, *((written.error,) if isinstance(written, Err) else ()))
    for error in errors:
        logger.error(error)
    logger.finish(actions=0)
    persisted = replace(snapshot, errors=(*errors, *logger.errors), files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
    snapshot = _write_snapshot(store, persisted, logger)
    try:
        run_data = ledger_source.collect(config.repo_root, config.phase, (), [])
        runs_error = None
    except (OSError, ValueError, RuntimeError) as error:
        run_data, runs_error = None, str(error)
    return StepResult(snapshot, next_state, run_data=run_data, runs_error=runs_error, failed_reads=logger.failed_reads)


def _closed_tasks(
    config: Config, phase: Result[Phase], tasks: Result[tuple[Task, ...]], logger: TickLog,
) -> Result[tuple[str, ...]]:
    """Ids of would-be dispatch candidates whose ATM task is closed. `atm task list` shows open tasks only, so each
    candidate's history is read once per tick with `atm task events`; no other bead costs a call."""
    if not isinstance(phase, Ok) or not isinstance(tasks, Ok):
        return Ok(())
    closed: list[str] = []
    for step in candidates(phase.value, tasks.value, frozenset()):
        result = logger.result("atm.task_events", atm_source.task_closed(step.bead_id, config.team, config.lead),
                               task_id=step.bead_id)
        if isinstance(result, Err):
            return result
        if result.value:
            closed.append(step.bead_id)
    return Ok(tuple(closed))


def step(config: Config, state: State, force_github: bool, store: StateStore) -> StepResult:
    """Run one best-effort tick using the caller-owned lock-backed store."""
    now = datetime.now(UTC)
    logger = TickLog(create_console_logger(config.repo_root))
    logger.event("tick_start", phase=config.phase)
    file = logger.result("phase_file", atm_bd.phase_file(config.repo_root, config.phase), phase=config.phase)
    repo = logger.result("repo_config", atm_bd.repo_config(config.repo_root))
    if isinstance(file, Err) or isinstance(repo, Err):
        errors = (*store.load_errors, *(item.error for item in (file, repo) if isinstance(item, Err)))
        snapshot = Snapshot(1, now, config.phase, False, None, (), (), (), errors, Metrics(0, 0, 0, 0),
                            _gh(config.github), None, None, "graph TD", (), config.interval_s, config.lead,
                            "", (), (), {})
        state_written = store.write_state(state)
        if isinstance(state_written, Err): snapshot = replace(snapshot, errors=(*snapshot.errors, state_written.error))
        for error in snapshot.errors:
            logger.error(error)
        logger.finish(actions=0)
        persisted = replace(snapshot, errors=(*snapshot.errors, *logger.errors), files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
        persisted = _write_snapshot(store, persisted, logger)
        return StepResult(persisted, state, failed_reads=logger.failed_reads)
    config = replace(config, lead=file.value.lead)
    root_read = beads_source.show((file.value.root_id,))
    if isinstance(root_read, Err) and hasattr(root_read.error, "fix"):
        # A failed `bd show` is a transient read failure, never "no root bead": keep the real error, say it is retried.
        root_read = Err(replace(root_read.error, fix=f"bd show {file.value.root_id} failed; retried next tick"))
    root = logger.result("beads.show_root", root_read, task_ids=[file.value.root_id])
    root_bead = root.value[0] if isinstance(root, Ok) and root.value else None
    # This is deliberately the sole read on a closed phase.
    if root_bead is not None and root_bead.status == SprintState.CLOSED:
        return _stopped_tick(config, state, now, store, file.value, repo.value, root_bead, logger, force_github)
    if not force_github and isinstance(root, Ok) and model.idle_stop_due(state, now):
        return _stopped_tick(config, state, now, store, file.value, repo.value, root_bead, logger)
    opened = logger.result("beads.open", beads_source.open_rows(file.value.label), label=file.value.label)
    ready = logger.result("beads.ready", beads_source.ready(file.value.label), label=file.value.label)
    known = opened.value.beads if isinstance(opened, Ok) else ()
    shown_ids = atm_bd.closed_show_ids(file.value, known)
    shown = logger.result("beads.show_closed", beads_source.show(shown_ids), task_ids=list(shown_ids))
    if isinstance(root, Err):
        phase = root
    elif isinstance(opened, Err):
        phase = opened
    elif isinstance(ready, Err):
        phase = ready
    elif isinstance(shown, Err):
        phase = shown
    else:
        phase = atm_bd.phase(file.value, root_bead, known, ready.value, shown.value)
    logger.result("phase", phase)
    run_data = None
    runs_error = None
    if isinstance(opened, Err):
        runs_error = str(opened.error)
    else:
        try:
            run_data = ledger_source.collect(config.repo_root, config.phase, opened.value.rows, [])
        except (OSError, ValueError, RuntimeError) as run_error:
            runs_error = str(run_error)
    members = logger.result("atm.members", atm_source.members(config.team, config.lead), team=config.team)
    tasks = logger.result("atm.tasks", atm_source.tasks(config.team, config.lead), team=config.team)
    closed_tasks = _closed_tasks(config, phase, tasks, logger)
    closed = frozenset(closed_tasks.value) if isinstance(closed_tasks, Ok) else frozenset()
    code_root = config.code_root or config.repo_root
    worktrees = logger.result("git.worktrees", git_source.worktrees(code_root))
    task_ids = tuple(task.id for task in tasks.value) if isinstance(tasks, Ok) else ()
    task_beads = logger.result("beads.show_tasks", beads_source.show(task_ids), task_ids=list(task_ids))
    if isinstance(task_beads, Ok) and task_beads.value:
        task_beads = logger.result("beads.task_ancestors", beads_source.ancestors(task_beads.value))
    due = model.due(
        state,
        phase.value,
        repo.value.bead_prefix,
        config.github,
        now,
        tasks.value if isinstance(tasks, Ok) else (),
        closed,
    ) if isinstance(phase, Ok) else None
    remotes = None
    if due and due.fetch and not config.no_fetch:
        fetched = logger.result("git.fetch", git_source.fetch(code_root))
        remotes = fetched if isinstance(fetched, Err) else None
        if isinstance(fetched, Ok):
            remotes = logger.result("git.remote_refs", git_source.remote_refs(code_root))
            if isinstance(remotes, Ok) and isinstance(phase, Ok):
                due = model.due(
                    replace(state, remotes=remotes.value), phase.value,
                    repo.value.bead_prefix, config.github, now,
                    tasks.value if isinstance(tasks, Ok) else (),
                    closed,
                )
    saved_merges = logger.result("cache.merges", store.load_merges())
    saved_prs = logger.result("cache.prs", store.load_prs())
    cached_prs = saved_prs.value if config.github != "off" and isinstance(saved_prs, Ok) else None
    gh = cached_prs.gh if cached_prs is not None else _gh(config.github)
    gh = replace(gh, poll_interval_s=config.github_poll_interval_s, quota_threshold=config.github_quota_threshold)
    prs = cached_prs.prs if cached_prs is not None else ()
    prs_read_at = cached_prs.read_at if cached_prs is not None else now
    prs_input = Ok(prs) if cached_prs is not None else None
    attempted_at = cached_prs.attempted_at if cached_prs is not None else None
    github_due = (attempted_at is None or cached_prs is None or cached_prs.gh.mode != config.github
                  or now - attempted_at >= timedelta(seconds=config.github_poll_interval_s))
    refresh_errors = []
    if config.github != "off" and (force_github or (due and github_due)):
        branch_groups = _branch_groups(phase.value, repo.value) if config.github == "rest" and isinstance(phase, Ok) else ()
        refreshed_prs, gh = _refresh_github(config, now, gh, branch_groups)
        attempted_at = now
        if isinstance(refreshed_prs, Ok):
            prs_input, prs, prs_read_at = refreshed_prs, refreshed_prs.value, now
        elif cached_prs is None:
            prs_input = refreshed_prs
        else:
            refresh_errors.append(refreshed_prs.error)
        logger.event("github_read", forced=force_github, outcome="paused" if gh.paused else "failed" if isinstance(refreshed_prs, Err) else "ok",
                     error=asdict(refreshed_prs.error) if isinstance(refreshed_prs, Err) else None)
    merges = dict(saved_merges.value.merges) if isinstance(saved_merges, Ok) else {}
    merge_results = []
    # A pair whose PR is closed or merged is not a merge candidate; a pair with no PR yet is still
    # checked, because dispatch verification of a ready step needs its merge result.
    open_pairs = {(pr.head, pr.base) for pr in prs if pr.state in (PrState.OPEN, PrState.DRAFT)}
    ended_pairs = {(pr.head, pr.base) for pr in prs if pr.state in (PrState.MERGED, PrState.CLOSED)} - open_pairs
    listed = remotes.value if isinstance(remotes, Ok) else state.remotes if remotes is None else None
    remote_branches = None if listed is None else {remote.branch for remote in listed}
    if due:
        for branch, target in due.merges:
            if (branch, target) in ended_pairs:
                if (cached := merges.get(branch)) is not None and cached.target == target:
                    del merges[branch]
                continue
            missing = None if remote_branches is None or (branch, target) not in open_pairs else (
                MergeState.HEAD_NOT_PUSHED if branch not in remote_branches else
                MergeState.BASE_DELETED if target not in remote_branches else None
            )
            if missing is not None:
                merges[branch] = Merge(branch, target, missing, (), (missing.value,), now)
                merge_results.append(Ok(merges[branch]))
                continue
            checked = remotes if isinstance(remotes, Err) else git_source.merge(code_root, branch, target)
            ref_missing = checked.error if isinstance(checked, Err) and isinstance(checked.error, NotFound) and (
                checked.error.what in (f"origin/{branch}", f"origin/{target}")) else None
            if not isinstance(remotes, Err) and ref_missing is None:
                checked = logger.result("git.merge", checked, branch=branch, target=target)
                merge_results.append(checked)
            if isinstance(checked, Ok):
                merges[branch] = checked.value
            elif ref_missing is not None:
                # A ref missing on origin is an unpushed branch or deleted base, not a failed merge check.
                missing = MergeState.HEAD_NOT_PUSHED if ref_missing.what == f"origin/{branch}" else MergeState.BASE_DELETED
                merges[branch] = Merge(branch, target, missing, (), (missing.value,), now)
                merge_results.append(Ok(merges[branch]))
            else:
                unavailable = Merge(branch, target, MergeState.ERROR, (), (verification_reason(checked.error),), now)
                merges[branch] = unavailable
                merge_results.append(Ok(unavailable))
    inputs = Inputs(now, config.lead, repo.value, config.interval_s, phase, members, tasks, worktrees, remotes, tuple(merge_results) if due else tuple(Ok(item) for item in merges.values()), prs_input, gh, task_beads, closed_tasks)
    snapshot, actions, next_state = model.tick(inputs, state, now, partial(atm_bd.dispatch_spec, templates=file.value.templates))
    steps = {item.bead_id: item for item in phase.value.steps} if isinstance(phase, Ok) else {}
    for section in snapshot.sections:
        if section.label == "Held":
            for held in section.rows:
                held_step = steps.get(held.bead_id)
                logger.event(
                    "hold", task_ids=[held.bead_id], recipient=held_step.assignee or config.lead if held_step else config.lead,
                    outcome="held", reason=held.reason,
                )
    def persist(store: StateStore):
            reported, action_errors = _run_actions(config, actions, store, now, logger)
            errors = [*store.load_errors, *action_errors, *refresh_errors]
            durable = state if config.dry_run else _durable_state(state, next_state, actions, reported)
            reported_sent = (*state.sent, *reported)[-5:]
            for written in (store.write_merges(MergeFile(now, merges)), store.write_prs(PrsFile(prs_read_at, gh, tuple(prs), attempted_at=attempted_at)), store.write_state(durable)):
                if isinstance(written, Err): errors.append(written.error)
            for error in (*snapshot.errors, *errors):
                logger.error(error)
            logger.finish(actions=len(actions))
            errors.extend(logger.errors)
            persisted = replace(snapshot, sent=reported_sent, errors=(*snapshot.errors, *errors), files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
            persisted = _write_snapshot(store, persisted, logger)
            return StepResult(persisted, durable, reported, run_data, runs_error, logger.failed_reads)
    return persist(store)
