from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from contract_samples import BEAD, CONFIG, PHASE, PHASE_FILE, REPO_CONFIG
from test_git_source import commit, git

from atm_bd_console import git_source, runner
from atm_bd_console.codec import to_json
from atm_bd_console.enums import MergeState
from atm_bd_console.result import CommandFailed, Err, Ok, Timeout
from atm_bd_console.state import acquire_lock, load_snapshot
from atm_bd_console.types import INITIAL_STATE, DispatchSpec, Merge, MergeFile

pytest_plugins = ("test_git_source",)


@pytest.fixture
def runtime(repo, tmp_path, monkeypatch):
    worktree = tmp_path / "clean-worktree"
    git(repo, "worktree", "add", "-q", str(worktree), "clean")
    candidate = replace(PHASE.steps[0], status="open", ready=True, anchor=False, quick_fix=False,
                        branch="clean", target="main", worktree=str(worktree))
    phase = replace(PHASE, closed=False, steps=(candidate,), step_errors=())
    events, assigned = [], []

    class Logger:
        def event(self, action, **fields):
            events.append((action, fields))
            return Ok(None)

        def flush(self): return Ok(None)

    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(phase))
    monkeypatch.setattr(runner.atm_bd, "dispatch_spec", lambda context, templates: Ok(DispatchSpec(templates["qa"], {"commit": context.commit})))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((replace(BEAD, status="open"),)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "send", lambda *_: Ok(None))
    monkeypatch.setattr(runner.atm_source, "assign", lambda *args: assigned.append(args) or Ok(None))
    config = replace(CONFIG, repo_root=str(repo), github="off", dry_run=False, no_fetch=False)
    state = replace(INITIAL_STATE, remotes=git_source.remote_refs(repo).value, last_cycle=datetime.now(UTC))
    with acquire_lock(repo, PHASE_FILE.root_id).value as store:
        clean = Merge("clean", "main", MergeState.CLEAN, (), (), datetime.now(UTC))
        assert store.write_merges(MergeFile(clean.checked_at, {"clean": clean})) == Ok(None)
        yield config, state, store, events, assigned


@pytest.mark.parametrize("failure", ("fetch", "remote_refs"))
def test_failed_refresh_rejects_cached_pin_and_recovers_fresh_commit(runtime, repo, tmp_path, monkeypatch, failure):
    config, state, store, events, assigned = runtime
    old_sha = git(repo, "rev-parse", "origin/clean")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    git(other, "checkout", "-q", "clean")
    fresh_sha = commit(other, "advance clean", **{"new.txt": "fresh\n"})
    git(other, "push", "-q", "origin", "clean")
    assert fresh_sha != old_sha
    original_refs = git_source.remote_refs
    if failure == "fetch":
        git(repo, "remote", "set-url", "origin", str(tmp_path / "unavailable.git"))
    else:
        monkeypatch.setattr(git_source, "remote_refs", lambda *_: Err(Timeout("timeout", ("git", "for-each-ref"), 30, "retry refs")))

    snapshot, held_state = runner.step(config, state, False, store)

    assert assigned == []
    assert held_state.remotes == ()
    assert store.load_state().value.remotes == ()
    assert any("origin verification unavailable" in problem.text for problem in snapshot.problems)
    assert snapshot.errors
    assert to_json(load_snapshot(repo, PHASE_FILE.root_id).value) == to_json(snapshot)
    assert store.load_merges().value.merges["clean"].state == MergeState.ERROR
    assert any(fields.get("source") == f"git.{failure}" and fields.get("outcome") == "failed" for _, fields in events)
    assert not any(fields.get("source") == "git.merge" for _, fields in events)
    if failure == "fetch":
        assert git(repo, "rev-parse", "origin/clean") == old_sha
    git(repo, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    monkeypatch.setattr(git_source, "remote_refs", original_refs)

    recovered, _ = runner.step(config, held_state, False, store)

    assert len(assigned) == 1 and assigned[0][2].vars["commit"] == fresh_sha
    assert recovered.sections == () and recovered.errors == ()
    assert [problem.key for problem in recovered.problems] == ["github_unavailable"]
    assert store.load_merges().value.merges["clean"].state == MergeState.CLEAN


@pytest.mark.parametrize("failure", ("missing", "timeout", "unsupported"))
def test_failed_merge_overwrites_cached_clean_and_recovery_resumes(runtime, repo, monkeypatch, failure):
    config, state, store, events, assigned = runtime
    original_run = git_source._run
    if failure == "missing":
        config = replace(config, no_fetch=True)
        git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    else:
        error = (Timeout("timeout", ("git", "merge-tree"), 30, "retry the merge check") if failure == "timeout" else
                 CommandFailed("command_failed", ("git", "merge-tree"), 129, "unknown option write-tree", "upgrade git"))
        monkeypatch.setattr(git_source, "_run", lambda path, *args, **kwargs:
                            Err(error) if args[0] == "merge-tree" else original_run(path, *args, **kwargs))

    snapshot, held_state = runner.step(config, state, False, store)

    assert assigned == []
    assert "merge verification unavailable" in snapshot.problems[0].text
    assert snapshot.errors
    cached = store.load_merges().value.merges["clean"]
    assert cached.state == MergeState.ERROR and cached.messages
    assert any(action == "hold" and "unavailable" in fields["reason"] for action, fields in events)
    assert any(fields.get("source") == "git.merge" and fields.get("error") == asdict(snapshot.errors[0]) for _, fields in events)
    monkeypatch.setattr(git_source, "_run", original_run)
    assert git_source.fetch(repo) == Ok(None)

    recovered, _ = runner.step(config, held_state, False, store)

    assert len(assigned) == 1
    assert recovered.errors == ()
    assert [problem.key for problem in recovered.problems] == ["github_unavailable"]
    assert store.load_merges().value.merges["clean"].state == MergeState.CLEAN


def test_no_fetch_explicitly_allows_cached_refs_but_still_probes_merge(runtime, repo, tmp_path):
    config, state, store, events, assigned = runtime
    git(repo, "remote", "set-url", "origin", str(tmp_path / "unavailable.git"))

    snapshot, _ = runner.step(replace(config, no_fetch=True), state, False, store)

    assert len(assigned) == 1
    assert snapshot.errors == ()
    assert not any(fields.get("source") == "git.fetch" for _, fields in events)
    assert any(fields.get("source") == "git.merge" and fields.get("outcome") == "ok" for _, fields in events)
