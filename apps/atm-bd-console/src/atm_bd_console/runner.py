"""Impure tick boundary; scheduling and decisions remain in :mod:`model`."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from . import atm_bd, atm_source, beads_source, git_source, github_source, model
from .log import create_console_logger
from .result import Err, Ok
from .state import acquire_lock, load_merges, load_prs
from .types import Action, ActionLine, Config, GhStatus, Inputs, MergeFile, PrsFile, State


def _gh(mode: str) -> GhStatus:
    return GhStatus(mode, None, None, None, False)  # type: ignore[arg-type]


def _run_actions(config: Config, actions: tuple[Action, ...], store, now: datetime):
    """Return boundary errors so they are visible in the persisted snapshot."""
    errors = []
    for action in actions:
        bead = action.bead_ids[0] if action.bead_ids else None
        if config.dry_run:
            logged = store.append_action(ActionLine(now, "dry_run", action.to, bead, action.text))
            if isinstance(logged, Err): errors.append(logged.error)
            continue
        if action.kind == "assign" and action.spec is not None and bead:
            result = atm_source.assign(action.to, bead, action.spec, config.lead)
        else:
            result = atm_source.send(action.to, action.text, config.lead)
        logged = store.append_action(ActionLine(now, action.kind, action.to, bead, action.text))
        if isinstance(logged, Err): errors.append(logged.error)
        if isinstance(result, Err):
            errors.append(result.error)
            held = store.append_action(ActionLine(now, "hold", action.to, bead, result.error.fix))
            if isinstance(held, Err): errors.append(held.error)
    return tuple(errors)


def step(config: Config, state: State, force_github: bool, store=None):
    """Run one best-effort tick and atomically persist its inspectable files."""
    now = datetime.now(UTC)
    file, repo = atm_bd.phase_file(config.repo_root, config.phase), atm_bd.repo_config(config.repo_root)
    if isinstance(file, Err) or isinstance(repo, Err):
        raise TypeError("phase and repository configuration must be valid")
    logger = create_console_logger(config.repo_root)
    if isinstance(logger, Ok): logger.value.event("tick_start", phase=config.phase)
    root = beads_source.show((file.value.root_id,))
    root_bead = root.value[0] if isinstance(root, Ok) and root.value else None
    # This is deliberately the sole read on a closed phase.
    if root_bead is not None and root_bead.status == "closed":
        phase = atm_bd.phase(file.value, root_bead, (), (), ())
        empty = Ok(())
        snapshot, _, next_state = model.tick(Inputs(now, config.lead, repo.value, config.interval_s, phase, empty, empty, empty, None, None, None, _gh(config.github), empty), state, now, atm_bd.dispatch_spec)
        return snapshot, next_state
    opened, ready = beads_source.open_beads(file.value.label), beads_source.ready(file.value.label)
    known = opened.value if isinstance(opened, Ok) else ()
    shown = beads_source.show(atm_bd.closed_show_ids(file.value, known))
    phase = atm_bd.phase(file.value, root_bead, known, ready.value if isinstance(ready, Ok) else (), shown.value if isinstance(shown, Ok) else ())
    members, tasks, worktrees = atm_source.members(config.lead), atm_source.tasks(config.lead), git_source.worktrees(config.repo_root)
    task_beads = beads_source.show(tuple(task.id for task in tasks.value) if isinstance(tasks, Ok) else ())
    due = model.due(state, phase.value, repo.value.bead_prefix, config.github, now) if isinstance(phase, Ok) else None
    if due and due.fetch and not config.no_fetch: git_source.fetch(config.repo_root)
    remotes = git_source.remote_refs(config.repo_root) if due and due.fetch else None
    saved_merges, saved_prs = load_merges(config.repo_root, file.value.root_id), load_prs(config.repo_root, file.value.root_id)
    merges = dict(saved_merges.value.merges) if isinstance(saved_merges, Ok) else {}
    if due:
        for branch, target in due.merges:
            checked = git_source.merge(config.repo_root, branch, target)
            if isinstance(checked, Ok): merges[branch] = checked.value
    gh = saved_prs.value.gh if isinstance(saved_prs, Ok) else _gh(config.github)
    prs = saved_prs.value.prs if isinstance(saved_prs, Ok) else ()
    prs_input = Ok(prs) if isinstance(saved_prs, Ok) else None
    if config.github != "off" and (force_github or (due and due.github)):
        prs_input, gh = github_source.open_prs(config.github)
        prs = prs_input.value if isinstance(prs_input, Ok) else ()
        if isinstance(logger, Ok): logger.value.event("github_read", forced=force_github)
    inputs = Inputs(now, config.lead, repo.value, config.interval_s, phase, members, tasks, worktrees, remotes, tuple(Ok(item) for item in merges.values()), prs_input, gh, task_beads)
    snapshot, actions, next_state = model.tick(inputs, state, now, atm_bd.dispatch_spec)
    lock = None if store is not None else acquire_lock(config.repo_root, file.value.root_id)
    active_store = store if store is not None else (lock.value if isinstance(lock, Ok) else None)
    if active_store is not None:
        def persist(store):
            errors = list(_run_actions(config, actions, store, now))
            for written in (store.write_merges(MergeFile(now, merges)), store.write_prs(PrsFile(now, gh, tuple(prs)),), store.write_state(next_state)):
                if isinstance(written, Err): errors.append(written.error)
            persisted = replace(snapshot, errors=(*snapshot.errors, *errors), files={"snapshot": str(store.directory / "snapshot.json"), "state": str(store.directory / "state.json")})
            store.write_snapshot(persisted)
            return persisted
        if store is not None:
            snapshot = persist(active_store)
        else:
            with active_store:
                snapshot = persist(active_store)
    if isinstance(logger, Ok): logger.value.event("tick_end", actions=len(actions))
    return snapshot, next_state
