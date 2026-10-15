from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from contract_samples import (
    ACTION,
    BEAD,
    COMMAND_FAILED,
    CONFIG,
    IO_FAILED,
    PHASE,
    PHASE_FILE,
    REPO_CONFIG,
    SNAPSHOT,
    STATE,
    TIMEOUT,
)

from atm_bd_console import runner
from atm_bd_console.result import Err, Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import Due, GhStatus


class Store:
    directory = Path(".")

    def append_action(self, _line): return Ok(None)
    def write_merges(self, _value): return Ok(None)
    def write_prs(self, _value): return Ok(None)
    def write_state(self, _value): return Ok(None)
    def write_snapshot(self, _value): return Ok(None)


@pytest.fixture
def github_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A runner fixture with no live sources and observable writes/logging."""
    calls: list[str] = []
    events: list[tuple[str, dict]] = []
    inputs = []

    class Store:
        directory = tmp_path

        def append_action(self, _line): return Ok(None)
        def write_merges(self, _value): return Ok(None)
        def write_prs(self, _value): return Ok(None)
        def write_state(self, _value): return Ok(None)
        def write_snapshot(self, _value): return Ok(None)

    class Logger:
        def event(self, action, **fields):
            events.append((action, fields))
            return Ok(None)

    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((BEAD,)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(PHASE))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(False, (), True))

    def tick(value, state, _now, _spec):
        inputs.append(value)
        errors = (value.prs.error,) if isinstance(value.prs, Err) else ()
        return replace(SNAPSHOT, gh=value.gh, errors=errors), (), state

    monkeypatch.setattr(runner.model, "tick", tick)

    def github(mode):
        calls.append(mode)
        status = GhStatus(mode, False, TIMEOUT, SNAPSHOT.generated_at, False)
        return Err(TIMEOUT), status

    monkeypatch.setattr(runner.github_source, "open_prs", github)
    return Store(), calls, events, inputs


def test_closed_root_stops_before_any_other_source_read(monkeypatch) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status="closed")
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closed root must not read another source")

    monkeypatch.setattr(runner.beads_source, "open_beads", forbidden)
    monkeypatch.setattr(runner.atm_source, "members", forbidden)
    snapshot, _ = runner.step(CONFIG, STATE, False, Store())
    assert snapshot.root_closed and snapshot.stopped == "closed"


def test_closed_root_persists_and_logs_tick_end(monkeypatch, tmp_path) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status="closed")
    writes, events = [], []

    class Store:
        directory = tmp_path
        def write_state(self, value): writes.append(("state", value)); return Ok(None)
        def write_snapshot(self, value): writes.append(("snapshot", value)); return Ok(None)

    class Logger:
        def event(self, action, **fields): events.append((action, fields)); return Ok(None)

    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    snapshot, next_state = runner.step(CONFIG, STATE, False, Store())
    assert [name for name, _ in writes] == ["state", "snapshot"]
    assert writes[0][1] == next_state and writes[1][1] == snapshot
    assert ("tick_end", {"actions": 0}) in events


def test_closed_root_write_failure_is_returned_in_snapshot(monkeypatch, tmp_path) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status="closed")
    class Store:
        directory = tmp_path
        def write_state(self, _value): return Err(IO_FAILED)
        def write_snapshot(self, _value): return Err(IO_FAILED)

    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    snapshot, _ = runner.step(CONFIG, STATE, False, Store())
    assert snapshot.errors.count(IO_FAILED) == 2


def test_github_off_never_calls_github(monkeypatch) -> None:
    monkeypatch.setattr(runner.github_source, "open_prs", lambda *_: (_ for _ in ()).throw(AssertionError("github")))
    # A malformed phase prevents due scheduling and is sufficient to exercise
    # the no-GitHub path without invoking real boundary commands.
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    runner.step(replace(CONFIG, github="off"), STATE, True, Store())


def test_forced_github_timeout_is_visible_and_logged_once(github_runtime) -> None:
    store, calls, events, inputs = github_runtime

    snapshot, _ = runner.step(CONFIG, STATE, True, store)

    assert calls == ["rest"]
    assert inputs[0].prs == Err(TIMEOUT)
    assert snapshot.gh.error == TIMEOUT and snapshot.errors == (TIMEOUT,)
    assert [event for event in events if event[0] == "github_read"] == [("github_read", {"forced": True})]


def test_github_off_skips_forced_read_and_event(github_runtime) -> None:
    store, calls, events, _ = github_runtime

    runner.step(replace(CONFIG, github="off"), STATE, True, store)

    assert calls == []
    assert not [event for event in events if event[0] == "github_read"]


def test_failed_action_is_returned_for_snapshot_errors(monkeypatch) -> None:
    class Store:
        def append_action(self, _line):
            return Ok(None)

    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: Err(COMMAND_FAILED))
    errors = runner._run_actions(replace(CONFIG, dry_run=False), (ACTION,), Store(), ACTION.at)
    assert errors == (COMMAND_FAILED,)


def test_second_atm_action_failure_is_persisted(github_runtime, monkeypatch) -> None:
    store, _, _, _ = github_runtime
    snapshots = []
    store.write_snapshot = lambda value: snapshots.append(value) or Ok(None)
    monkeypatch.setattr(runner.model, "tick", lambda _inputs, state, _now, _spec: (SNAPSHOT, (ACTION, ACTION), state))
    outcomes = iter((Ok(None), Err(COMMAND_FAILED)))
    monkeypatch.setattr(runner.atm_source, "send", lambda *_: next(outcomes))
    snapshot, _ = runner.step(CONFIG, STATE, False, store)
    assert COMMAND_FAILED in snapshot.errors
    assert snapshots[-1].errors == snapshot.errors


def test_read_only_store_write_failure_is_visible(github_runtime) -> None:
    store, _, _, _ = github_runtime
    store.write_snapshot = lambda _value: Err(IO_FAILED)
    snapshot, _ = runner.step(CONFIG, STATE, False, store)
    assert IO_FAILED in snapshot.errors


def test_runner_reports_real_read_only_store_write_failure(github_runtime, tmp_path) -> None:
    _, _, _, _ = github_runtime
    acquired = acquire_lock(tmp_path, "atm-phase-bh")
    assert isinstance(acquired, Ok)
    directory = acquired.value.directory
    with acquired.value as store:
        directory.chmod(0o500)
        try:
            snapshot, _ = runner.step(replace(CONFIG, repo_root=str(tmp_path)), STATE, False, store)
        finally:
            directory.chmod(0o700)
    assert any(error.kind == "io_failed" for error in snapshot.errors)


def test_step_reads_members_and_tasks_for_config_team_never_the_lead(fake_bin, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    fake_bin.make("atm", 'printf "{}"\n')
    config = replace(CONFIG, lead="fenix", team="atm-dev", github="off", repo_root=str(tmp_path))

    runner.step(config, STATE, True, Store())

    argv = fake_bin.argv()
    assert ("atm", "members", "--team", "atm-dev", "--json") in argv
    assert ("atm", "task", "list", "--all", "--json", "--team", "atm-dev") in argv
    assert all(command[command.index("--team") + 1] != "fenix" for command in argv if "--team" in command)
