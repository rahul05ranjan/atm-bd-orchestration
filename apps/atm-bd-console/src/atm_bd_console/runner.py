"""Impure tick boundary; scheduling and decisions remain in :mod:`model`."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from . import atm_bd, atm_source, beads_source, git_source, github_source, model
from .log import create_console_logger
from .result import Err, Ok
from .state import StateStore
from .types import Action, ActionLine, Config, GhStatus, Inputs, MergeFile, Metrics, PrsFile, Snapshot, State


def _gh(mode: Literal["graphql", "rest", "off"]) -> GhStatus:
    return GhStatus(mode, None, None, None, False)


def _run_actions(config: Config, actions: tuple[Action, ...], store: StateStore, now: datetime) -> tuple[tuple[Action, ...], tuple]:
    """Run actions and retain their actual delivery outcome for reporting."""
    errors = []
    reported = []
    for action in actions:
        bead = action.bead_ids[0] if action.bead_ids else None
        if config.dry_run:
            logged = store.append_action(ActionLine(now, "dry_run", action.to, bead, action.text))
            if isinstance(logged, Err): errors.append(logged.error)
            reported.append(replace(action, outcome="dry_run"))
            continue
        if action.kind == "assign" and action.spec is not None and bead:
            result = atm_source.assign(action.to, bead, action.spec, config.lead, config.team)
        else:
            result = atm_source.send(action.to, action.text, config.lead, config.team)
        logged = store.append_action(ActionLine(now, action.kind, action.to, bead, action.text))
        if isinstance(logged, Err): errors.append(logged.error)
        if isinstance(result, Err):
            errors.append(result.error)
            held = store.append_action(ActionLine(now, "hold", action.to, bead, result.error.fix))
            if isinstance(held, Err): errors.append(held.error)
            reported.append(replace(action, outcome=f"failed {result.error.kind}"))
        else:
            reported.append(replace(action, outcome="sent"))
    return tuple(reported), tuple(errors)


def _durable_state(state: State, proposed: State, actions: tuple[Action, ...], reported: tuple[Action, ...]) -> State:
    """Commit only effects whose boundary call actually succeeded.

    A new problem is a delivery obligation, not merely an observation.  A
    failed first send must therefore be absent from durable state so the next
    tick proposes it again; a failed batch keeps its previous due time.
    """
    successful = tuple(action for action in reported if action.outcome == "sent")
    failed = tuple(action for action in reported if action.outcome and action.outcome.startswith("failed "))
    previous = {problem.key: problem for problem in state.problems}
    failed_new = {
        action.bead_ids[0]
        for action in failed
        if action.kind == "send" and action.bead_ids
    }
    problems = tuple(
        problem
        for problem in proposed.problems
        if problem.key in previous or problem.bead_id not in failed_new
    )
    failed_batches = {action.to for action in failed if action.kind == "batch"}
    batch_due = dict(proposed.batch_due)
    for recipient in failed_batches:
        if recipient in state.batch_due:
            batch_due[recipient] = state.batch_due[recipient]
        else:
            batch_due.pop(recipient, None)
    for recipient in tuple(batch_due):
        if not any(problem.to == recipient for problem in problems):
            batch_due.pop(recipient)
    notices_succeeded = any(action.kind in ("send", "batch") for action in successful)
    return replace(
        proposed,
        problems=problems,
        cycle_first_send=proposed.cycle_first_send if notices_succeeded else state.cycle_first_send,
        last_batch=proposed.last_batch if any(action.kind == "batch" for action in successful) else state.last_batch,
        sent=(*state.sent, *successful)[-5:],
        batch_due=batch_due,
    )


def _stopped_tick(config: Config, state: State, now: datetime, store: StateStore, file, repo, root_bead, logger):
    phase = atm_bd.phase(file, root_bead, (), (), ())
    if root_bead is not None and root_bead.status == "closed" and isinstance(phase, Ok):
        phase = Ok(replace(phase.value, step_errors=()))
    empty = Ok(())
    snapshot, _, next_state = model.tick(
        Inputs(now, config.lead, repo, config.interval_s, phase, empty, empty, empty, None, None, None, _gh(config.github), empty),
        state,
        now,
        atm_bd.dispatch_spec,
    )
    next_state = state if config.dry_run else next_state
    written = store.write_state(next_state)
    errors = (*snapshot.errors, *store.load_errors, *((written.error,) if isinstance(written, Err) else ()))
    persisted = replace(snapshot, errors=errors, files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
    snapshot_written = store.write_snapshot(persisted)
    snapshot = replace(persisted, errors=(*persisted.errors, snapshot_written.error)) if isinstance(snapshot_written, Err) else persisted
    if isinstance(logger, Ok): logger.value.event("tick_end", actions=0)
    return snapshot, next_state


def step(config: Config, state: State, force_github: bool, store: StateStore):
    """Run one best-effort tick using the caller-owned lock-backed store."""
    now = datetime.now(UTC)
    file, repo = atm_bd.phase_file(config.repo_root, config.phase), atm_bd.repo_config(config.repo_root)
    if isinstance(file, Err) or isinstance(repo, Err):
        errors = (*store.load_errors, *(item.error for item in (file, repo) if isinstance(item, Err)))
        snapshot = Snapshot(1, now, config.phase, False, None, (), (), (), errors, Metrics(0, 0, 0, 0),
                            _gh(config.github), None, None, "graph TD", (), config.interval_s, config.lead,
                            "", (), (), {})
        logger = create_console_logger(config.repo_root)
        if isinstance(logger, Ok): logger.value.event("tick_start", phase=config.phase)
        state_written = store.write_state(state)
        if isinstance(state_written, Err): snapshot = replace(snapshot, errors=(*snapshot.errors, state_written.error))
        persisted = replace(snapshot, files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
        snapshot_written = store.write_snapshot(persisted)
        if isinstance(snapshot_written, Err): persisted = replace(persisted, errors=(*persisted.errors, snapshot_written.error))
        if isinstance(logger, Ok): logger.value.event("tick_end", actions=0)
        return persisted, state
    logger = create_console_logger(config.repo_root)
    if isinstance(logger, Ok): logger.value.event("tick_start", phase=config.phase)
    root = beads_source.show((file.value.root_id,))
    root_bead = root.value[0] if isinstance(root, Ok) and root.value else None
    # This is deliberately the sole read on a closed phase.
    if root_bead is not None and root_bead.status == "closed":
        return _stopped_tick(config, state, now, store, file.value, repo.value, root_bead, logger)
    if not force_github and model.idle_stop_due(state, now):
        return _stopped_tick(config, state, now, store, file.value, repo.value, root_bead, logger)
    opened, ready = beads_source.open_beads(file.value.label), beads_source.ready(file.value.label)
    known = opened.value if isinstance(opened, Ok) else ()
    shown = beads_source.show(atm_bd.closed_show_ids(file.value, known))
    if isinstance(opened, Err):
        phase = opened
    elif isinstance(ready, Err):
        phase = ready
    elif isinstance(shown, Err):
        phase = shown
    else:
        phase = atm_bd.phase(file.value, root_bead, known, ready.value, shown.value)
    members, tasks, worktrees = atm_source.members(config.team), atm_source.tasks(config.team), git_source.worktrees(config.repo_root)
    task_beads = beads_source.show(tuple(task.id for task in tasks.value) if isinstance(tasks, Ok) else ())
    due = model.due(
        state,
        phase.value,
        repo.value.bead_prefix,
        config.github,
        now,
        tasks.value if isinstance(tasks, Ok) else (),
    ) if isinstance(phase, Ok) else None
    remotes = None
    if due and due.fetch and not config.no_fetch:
        fetched = git_source.fetch(config.repo_root)
        if isinstance(fetched, Ok):
            remotes = git_source.remote_refs(config.repo_root)
            if isinstance(remotes, Ok) and isinstance(phase, Ok):
                due = model.due(
                    replace(state, remotes=remotes.value), phase.value,
                    repo.value.bead_prefix, config.github, now,
                    tasks.value if isinstance(tasks, Ok) else (),
                )
    saved_merges, saved_prs = store.load_merges(), store.load_prs()
    merges = dict(saved_merges.value.merges) if isinstance(saved_merges, Ok) else {}
    merge_results = []
    if due:
        for branch, target in due.merges:
            checked = git_source.merge(config.repo_root, branch, target)
            merge_results.append(checked)
            if isinstance(checked, Ok): merges[branch] = checked.value
    cached_prs = saved_prs.value if config.github != "off" and isinstance(saved_prs, Ok) else None
    gh = cached_prs.gh if cached_prs is not None else _gh(config.github)
    prs = cached_prs.prs if cached_prs is not None else ()
    prs_read_at = cached_prs.read_at if cached_prs is not None else now
    prs_input = Ok(prs) if cached_prs is not None else None
    if config.github != "off" and (force_github or (due and due.github)):
        refreshed_prs, gh = github_source.open_prs(config.github)
        if isinstance(refreshed_prs, Ok):
            prs_input = refreshed_prs
            prs = refreshed_prs.value
            prs_read_at = now
        elif cached_prs is None:
            prs_input = refreshed_prs
        if isinstance(logger, Ok): logger.value.event("github_read", forced=force_github)
    inputs = Inputs(now, config.lead, repo.value, config.interval_s, phase, members, tasks, worktrees, remotes, tuple(merge_results) if due else tuple(Ok(item) for item in merges.values()), prs_input, gh, task_beads)
    snapshot, actions, next_state = model.tick(inputs, state, now, atm_bd.dispatch_spec)
    def persist(store: StateStore):
            reported, action_errors = _run_actions(config, actions, store, now)
            errors = [*store.load_errors, *action_errors]
            durable = state if config.dry_run else _durable_state(state, next_state, actions, reported)
            reported_sent = (*state.sent, *reported)[-5:]
            for written in (store.write_merges(MergeFile(now, merges)), store.write_prs(PrsFile(prs_read_at, gh, tuple(prs)),), store.write_state(durable)):
                if isinstance(written, Err): errors.append(written.error)
            persisted = replace(snapshot, sent=reported_sent, errors=(*snapshot.errors, *errors), files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
            snapshot_written = store.write_snapshot(persisted)
            if isinstance(snapshot_written, Err):
                persisted = replace(persisted, errors=(*persisted.errors, snapshot_written.error))
            return persisted, durable
    snapshot, next_state = persist(store)
    if isinstance(logger, Ok): logger.value.event("tick_end", actions=len(actions))
    return snapshot, next_state
