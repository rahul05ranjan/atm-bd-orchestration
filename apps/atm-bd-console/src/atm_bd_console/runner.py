"""Runtime boundary: gather one tick's inputs, run the pure model, persist it."""
from __future__ import annotations

from datetime import UTC, datetime

from . import atm_bd, atm_source, beads_source, git_source, github_source, model
from .result import Err, Ok
from .state import load_merges, load_prs
from .types import Config, GhStatus, Inputs, State


def _empty_gh(mode: str) -> GhStatus:
    return GhStatus(mode, None, None, None, False)


def step(config: Config, state: State, force_github: bool) -> tuple[object, State]:
    """Run one best-effort tick; source failures remain typed input results."""
    now = datetime.now(UTC)
    phase_file = atm_bd.phase_file(config.repo_root, config.phase)
    repo = atm_bd.repo_config(config.repo_root)
    if isinstance(phase_file, Err) or isinstance(repo, Err):
        raise TypeError("phase and repository configuration must be valid before running")
    root = beads_source.show((phase_file.value.root_id,))
    open_beads = beads_source.open_beads(phase_file.value.label)
    ready = beads_source.ready(phase_file.value.label)
    shown = beads_source.show(atm_bd.closed_show_ids(phase_file.value, open_beads.value if isinstance(open_beads, Ok) else ()))
    phase = atm_bd.phase(phase_file.value, root.value[0] if isinstance(root, Ok) and root.value else None,
                         open_beads.value if isinstance(open_beads, Ok) else (),
                         ready.value if isinstance(ready, Ok) else (), shown.value if isinstance(shown, Ok) else ())
    members = atm_source.members(config.lead)
    tasks = atm_source.tasks(config.lead)
    worktrees = git_source.worktrees(config.repo_root)
    remotes = git_source.remote_refs(config.repo_root)
    cached_merges = load_merges(config.repo_root, phase_file.value.root_id)
    cached_prs = load_prs(config.repo_root, phase_file.value.root_id)
    gh, prs = _empty_gh(config.github), cached_prs
    if force_github and config.github != "off":
        prs, gh = github_source.open_prs(config.github)
    merges = tuple(Ok(value) for value in (cached_merges.value.merges.values() if isinstance(cached_merges, Ok) else ()))
    task_beads = beads_source.show(tuple(task.id for task in tasks.value if isinstance(tasks, Ok)))
    inputs = Inputs(now, config.lead, repo.value, config.interval_s, phase, members, tasks, worktrees, remotes,
                    merges, prs, gh, task_beads)
    return model.tick(inputs, state, now, atm_bd.dispatch_spec)[::2]
