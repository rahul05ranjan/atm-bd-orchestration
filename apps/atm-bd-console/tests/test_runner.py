from __future__ import annotations

import os
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from contract_samples import (
    ACTION,
    BEAD,
    COMMAND_FAILED,
    CONFIG,
    EARLIER,
    IO_FAILED,
    MERGE_FILE,
    NOW,
    PHASE,
    PHASE_FILE,
    PRS_FILE,
    REMOTE_REF,
    REPO_CONFIG,
    SNAPSHOT,
    STATE,
    TIMEOUT,
)

from atm_bd_console import log, runner
from atm_bd_console.enums import QaState, SprintState
from atm_bd_console.log import TickLog, error_message
from atm_bd_console.model import due as model_due
from atm_bd_console.model import tick as model_tick
from atm_bd_console.result import ConfigInvalid, Err, IoFailed, NotFound, Ok, ParseFailed
from atm_bd_console.state import acquire_lock, load_prs, load_snapshot, load_state
from atm_bd_console.types import (
    INITIAL_STATE,
    ActionLine,
    Bead,
    Due,
    GhStatus,
    Member,
    PhaseFile,
    Problem,
    RemoteRef,
    Section,
    SectionRow,
    Task,
)

RUNNING_STATE = replace(STATE, idle_since=None)


class StoreReads:
    load_errors = ()

    def load_merges(self): return Err(NotFound("not_found", "merge.json", "run"))
    def load_prs(self): return Err(NotFound("not_found", "prs.json", "run"))
    def load_actions(self): return Ok((ActionLine(ACTION.at, "assign", "quality-mgr", "atm-bh-6.qa", "QA completed"),))


class Store(StoreReads):
    directory = Path(".")

    def append_action(self, _line): return Ok(None)
    def write_merges(self, _value): return Ok(None)
    def write_prs(self, _value): return Ok(None)
    def write_state(self, _value): return Ok(None)
    def write_snapshot(self, _value): return Ok(None)


@pytest.fixture(autouse=True)
def isolate_console_logger(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[object, ...]] = []

    class Logger:
        def event(self, _action, **_fields): return Ok(None)
        def flush(self): return Ok(None)

    def fake_factory(*args):
        calls.append(args)
        return Ok(Logger())

    monkeypatch.setattr(runner, "create_console_logger", fake_factory)
    yield calls


def test_isolated_runner_uses_fake_logger_factory(isolate_console_logger):
    runner.step(CONFIG, STATE, False, Store())

    assert isolate_console_logger == [(CONFIG.repo_root,)]


@pytest.fixture
def github_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A runner fixture with no live sources and observable writes/logging."""
    calls: list[str] = []
    events: list[tuple[str, dict]] = []
    inputs = []

    class Store(StoreReads):
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

        def flush(self): return Ok(None)

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
        errors = [value.prs.error] if isinstance(value.prs, Err) else []
        return replace(SNAPSHOT, gh=value.gh, errors=tuple(errors)), (), state

    monkeypatch.setattr(runner.model, "tick", tick)

    def github(mode, *_args):
        calls.append(mode)
        status = GhStatus(mode, False, TIMEOUT, SNAPSHOT.generated_at, False)
        return Err(TIMEOUT), status

    monkeypatch.setattr(runner.github_source, "open_prs", github)
    return Store(), calls, events, inputs


def test_closed_root_hydrates_history_without_dispatch_or_fetch(monkeypatch) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.CLOSED)
    shown = []

    def initial_read(value):
        assert not shown
        return Ok(value)

    def show(ids):
        shown.append(ids)
        return Ok((closed,)) if ids == (PHASE_FILE.root_id,) else Ok((BEAD,))

    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: initial_read(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: initial_read(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", show)
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(PHASE))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("stopped history must not dispatch or fetch")

    store = Store()
    monkeypatch.setattr(runner, "load_prs", lambda *_: Ok(PRS_FILE))
    monkeypatch.setattr(runner, "load_merges", lambda *_: Ok(MERGE_FILE))
    monkeypatch.setattr(runner, "load_snapshot", lambda *_: Ok(SNAPSHOT))
    for source, name in (
        (runner.atm_source, "send"), (runner.atm_source, "assign"),
        (runner.git_source, "fetch"),
        (runner.git_source, "remote_refs"), (runner.git_source, "merge"),
        (runner.github_source, "open_prs"), (runner.atm_bd, "dispatch_spec"),
        (store, "append_action"), (store, "write_merges"), (store, "write_prs"),
    ):
        monkeypatch.setattr(source, name, forbidden)
    snapshot, _ = runner.step(CONFIG, STATE, False, store)
    assert shown == [(PHASE_FILE.root_id,), (
        "atm-bh-1", "atm-bh-1.group-dev", "atm-bh-1.group-sanity", "atm-bh-1.group-qa",
        "atm-bh-6", "atm-bh-6.group-dev", "atm-bh-6.group-sanity", "atm-bh-6.group-qa",
    )]
    assert snapshot.root_closed and snapshot.stopped == "closed"
    assert snapshot.agents == SNAPSHOT.agents
    assert [row.pr.number for row in snapshot.prs] == [PRS_FILE.prs[0].number]
    assert [(action.text, action.outcome) for action in snapshot.sent] == [("QA completed", "recorded")]
    assert snapshot.errors == ()
    assert snapshot.gh.error == PRS_FILE.gh.error


def test_runner_uses_closed_qa_reason_after_task_leaves_active_queue(monkeypatch) -> None:
    """A closed QA bead from the normal batched runner read drives the following tick's icon state."""
    root_id = "atm-phase-bi"
    sprint_id = "atm-bi-5"
    qa_id = f"{sprint_id}.group-qa"
    file = PhaseFile("bi", root_id, "phase:bi", (sprint_id,), {sprint_id: ()}, "lumen", {})
    root = Bead(root_id, "Phase BI", SprintState.OPEN, ("phase:bi",), None, (), None, {}, NOW, None, None)
    sprint = Bead(
        sprint_id, "bi-5", SprintState.IN_PROGRESS, ("phase:bi", "stage:sprint"), root_id, (), None,
        {"layer": 5, "branch": "sprint/bi-5", "pr_target": "integrate/phase-bi", "stack": "phase-bi"},
        NOW, None, None,
    )
    qa_metadata = {"checked_bead": f"{sprint_id}.group-dev", "sprint_bead": sprint_id,
                   "sprint": "bi-5", "round": 1}
    active_qa = Bead(
        qa_id, "QA round 1", SprintState.IN_PROGRESS, ("phase:bi", "stage:qa"), sprint_id, (), "quality-mgr",
        qa_metadata, NOW, NOW, None,
    )
    closed_qa = replace(active_qa, status=SprintState.CLOSED, closed_at=NOW, close_reason="PASS: 0 findings filed")
    phase_file = lambda *_: Ok(file)
    repo_config = lambda *_: Ok(REPO_CONFIG)
    active = [True]
    seen_inputs = []
    task = Task(qa_id, "quality-mgr", "active", 0, NOW, NOW, "QA round 1")

    def show(ids):
        if ids == (root_id,):
            return Ok((root,))
        if qa_id in ids:
            return Ok((sprint, closed_qa))
        if not ids:
            return Ok(())
        return Ok((sprint,))

    def open_beads(_label):
        return Ok((active_qa,) if active[0] else ())

    monkeypatch.setattr(runner.atm_bd, "phase_file", phase_file)
    monkeypatch.setattr(runner.atm_bd, "repo_config", repo_config)
    monkeypatch.setattr(runner.beads_source, "show", show)
    monkeypatch.setattr(runner.beads_source, "open_beads", open_beads)
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    def members(*_args):
        return Ok((Member("quality-mgr", "active", NOW, agent_type="qa"),))

    def tasks(*_args):
        return Ok((task,) if active[0] else ())

    monkeypatch.setattr(runner.atm_source, "members", members)
    monkeypatch.setattr(runner.atm_source, "tasks", tasks)
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_args, **_kwargs: Due(False, (), False))
    original_tick = runner.model.tick

    def capture_tick(value, *args):
        seen_inputs.append(value)
        return original_tick(value, *args)

    monkeypatch.setattr(runner.model, "tick", capture_tick)
    config = replace(CONFIG, phase="bi", github="off", no_fetch=True, dry_run=True)
    store = Store()

    first, _ = runner.step(config, RUNNING_STATE, False, store)
    assert first.agents[0].qa_state is QaState.RUNNING
    active[0] = False
    second, _ = runner.step(config, RUNNING_STATE, False, store)

    assert seen_inputs[1].tasks == Ok(())
    assert seen_inputs[1].phase.value.steps[0].close_reason == "PASS: 0 findings filed"
    assert second.agents[0].qa_state is QaState.PASS


def test_stopped_tick_surfaces_malformed_action_history_from_real_store(monkeypatch, tmp_path) -> None:
    closed = replace(BEAD, id=PHASE_FILE.root_id, status=SprintState.CLOSED)
    config = replace(CONFIG, repo_root=str(tmp_path))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=True, steps=(), step_errors=())))
    locked = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(locked, Ok)

    with locked.value as store:
        actions_path = store.directory / "actions.jsonl"
        for index in range(6):
            assert store.append_action(ActionLine(
                ACTION.at + timedelta(minutes=index), "send", "quality-mgr", "atm-bh-6.qa", f"action-{index}",
            )) == Ok(None)
        with actions_path.open("a", encoding="utf-8") as handle:
            handle.write("{not-json}\n")
        snapshot, _ = runner.step(config, STATE, False, store)

    assert snapshot.stopped == "closed"
    assert [action.text for action in snapshot.sent] == [f"action-{index}" for index in range(1, 6)]
    assert len(snapshot.errors) == 1
    assert isinstance(snapshot.errors[0], ParseFailed)
    assert snapshot.errors[0].source == f"{actions_path}:7"


def test_closed_phase_forced_github_refresh_uses_sprint_groups(monkeypatch, tmp_path):
    closed = replace(BEAD, id=PHASE_FILE.root_id, status=SprintState.CLOSED)
    config = replace(CONFIG, repo_root=str(tmp_path), github="rest", dry_run=True)
    calls = []
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=True, step_errors=())))
    monkeypatch.setattr(runner.github_source, "open_prs", lambda *args: (
        calls.append(args) or Ok(()), GhStatus("rest", True, None, NOW, False)))
    monkeypatch.setattr(runner.atm_source, "send", lambda *_: pytest.fail("unexpected send"))
    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: pytest.fail("unexpected assignment"))
    locked = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(locked, Ok)
    with locked.value as store:
        snapshot, _ = runner.step(config, INITIAL_STATE, True, store)
    assert snapshot.stopped == "closed" and snapshot.gh.ok is True
    assert calls[0][:3] == ("rest", str(tmp_path), runner._branch_groups(PHASE, REPO_CONFIG))
    assert isinstance(calls[0][3], runner.github_source.Budget)


def test_closed_phase_forced_refresh_respects_pause_and_keeps_cached_prs(monkeypatch, tmp_path):
    closed = replace(BEAD, id=PHASE_FILE.root_id, status=SprintState.CLOSED)
    config = replace(CONFIG, repo_root=str(tmp_path), github="rest", dry_run=True)
    status = replace(GhStatus("rest", False, None, NOW, False), paused=True,
                     quota_remaining=2450, quota_limit=5000, quota_reset=NOW + timedelta(hours=1))
    cached = replace(PRS_FILE, gh=status)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return NOW

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=True, step_errors=())))
    monkeypatch.setattr(runner.github_source.command, "run", lambda *_args, **_kwargs: pytest.fail("paused GitHub request"))
    locked = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(locked, Ok)
    with locked.value as store:
        assert isinstance(store.write_prs(cached), Ok)
        snapshot, _ = runner.step(config, INITIAL_STATE, True, store)
        saved = store.load_prs()
    assert snapshot.gh.paused
    assert isinstance(saved, Ok) and saved.value.prs == cached.prs and saved.value.read_at == cached.read_at


def test_runner_passes_the_configured_interval_to_inputs(github_runtime) -> None:
    store, _, _, inputs = github_runtime
    config = replace(CONFIG, interval_s=137)

    runner.step(config, RUNNING_STATE, False, store)

    assert len(inputs) == 1 and inputs[0].interval_s == config.interval_s


def test_idle_stop_skips_every_expensive_source_unless_forced(monkeypatch) -> None:
    root = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.OPEN)
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, steps=())))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("idle-stop must not poll this boundary")

    for source, name in (
        (runner.beads_source, "open_beads"), (runner.beads_source, "ready"),
        (runner.atm_source, "members"), (runner.atm_source, "tasks"),
        (runner.git_source, "worktrees"), (runner.git_source, "fetch"),
        (runner.git_source, "remote_refs"), (runner.git_source, "merge"),
        (runner.github_source, "open_prs"),
    ):
        monkeypatch.setattr(source, name, forbidden)

    snapshot, _ = runner.step(CONFIG, replace(STATE, idle_since=ACTION.at - timedelta(minutes=60)), False, Store())

    assert snapshot.stopped == "idle"


def test_forced_refresh_resumes_when_work_became_active(monkeypatch, tmp_path) -> None:
    root = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.OPEN)
    active = replace(PHASE, closed=False)
    calls: list[str] = []
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(active))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(False, (), True))
    monkeypatch.setattr(
        runner.github_source,
        "open_prs",
        lambda mode, *_args: calls.append(mode) or (Ok(()), GhStatus(mode, True, None, None, False)),
    )

    class Store(StoreReads):
        directory = tmp_path

        def write_merges(self, _value): return Ok(None)
        def write_prs(self, _value): return Ok(None)
        def write_state(self, _value): return Ok(None)
        def write_snapshot(self, _value): return Ok(None)

    snapshot, next_state = runner.step(
        replace(CONFIG, dry_run=False),
        replace(STATE, idle_since=ACTION.at - timedelta(minutes=60)),
        True,
        Store(),
    )

    assert calls == ["rest"]
    assert snapshot.stopped is None and next_state.idle_since is None


def test_forced_github_read_leaves_an_idle_cron_stopped(monkeypatch, tmp_path) -> None:
    root = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.OPEN)
    calls: list[str] = []
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, steps=())))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(False, (), True))
    monkeypatch.setattr(
        runner.github_source,
        "open_prs",
        lambda mode, *_args: calls.append(mode) or (Ok(()), GhStatus(mode, True, None, None, False)),
    )

    class Store(StoreReads):
        directory = tmp_path

        def write_merges(self, _value): return Ok(None)
        def write_prs(self, _value): return Ok(None)
        def write_state(self, _value): return Ok(None)
        def write_snapshot(self, _value): return Ok(None)

    snapshot, _ = runner.step(
        replace(CONFIG, dry_run=False),
        replace(STATE, idle_since=ACTION.at - timedelta(minutes=60)),
        True,
        Store(),
    )

    assert calls == ["rest"]
    assert snapshot.stopped == "idle"


def test_closed_root_persists_and_logs_tick_end(monkeypatch, tmp_path) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.CLOSED)
    writes, events = [], []

    class Store(StoreReads):
        directory = tmp_path
        def write_state(self, value): writes.append(("state", value)); return Ok(None)
        def write_snapshot(self, value): writes.append(("snapshot", value)); return Ok(None)

    class Logger:
        def event(self, action, **fields): events.append((action, fields)); return Ok(None)
        def flush(self): return Ok(None)

    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    snapshot, next_state = runner.step(CONFIG, STATE, False, Store())
    assert [name for name, _ in writes] == ["state", "snapshot"]
    assert writes[0][1] == next_state and writes[1][1] == snapshot
    assert ("tick_end", {"level": "info", "message": None, "actions": 0}) in events


@pytest.mark.parametrize("stopped", ("closed", "idle"))
@pytest.mark.parametrize("dry_run", (True, False))
def test_stopped_tick_preserves_durable_state_in_dry_run(monkeypatch, tmp_path, stopped, dry_run) -> None:
    now = ACTION.at
    previous = replace(STATE, last_cycle=now - timedelta(minutes=20), idle_since=now - timedelta(minutes=61))
    root = replace(BEAD, id=PHASE_FILE.root_id, status=SprintState.CLOSED if stopped == "closed" else SprintState.OPEN)
    config = replace(CONFIG, repo_root=str(tmp_path), dry_run=dry_run)

    class Clock(datetime):
        @classmethod
        def now(cls, _timezone): return now

    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=stopped == "closed", steps=(), step_errors=())))
    locked = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(locked, Ok)

    with locked.value as store:
        assert isinstance(store.write_state(previous), Ok)
        state_path = store.directory / "state.json"
        original_bytes = state_path.read_bytes()
        snapshot, next_state = runner.step(config, previous, False, store)

        assert snapshot.stopped == stopped
        assert snapshot.generated_at == now
        assert not snapshot.errors
        assert load_snapshot(tmp_path, PHASE_FILE.root_id) == Ok(snapshot)
        assert load_state(tmp_path, PHASE_FILE.root_id) == Ok(next_state)
        if dry_run:
            assert next_state == previous
            assert state_path.read_bytes() == original_bytes
        else:
            assert next_state.last_cycle == now
            assert not next_state.problems
            assert next_state != previous


def test_closed_root_write_failure_is_returned_in_snapshot(monkeypatch, tmp_path) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status=SprintState.CLOSED)
    class Store(StoreReads):
        directory = tmp_path
        def write_state(self, _value): return Err(IO_FAILED)
        def write_snapshot(self, _value): return Err(IO_FAILED)

    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))
    snapshot, _ = runner.step(CONFIG, STATE, False, Store())
    assert snapshot.errors == (IO_FAILED, IO_FAILED)


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


def test_due_uses_successfully_read_tasks_to_skip_extra_reads(github_runtime, monkeypatch) -> None:
    store, _, _, _ = github_runtime
    candidate = replace(PHASE.steps[0], status=SprintState.OPEN, anchor=False, quick_fix=False, ready=True)
    phase = replace(PHASE, closed=False, step_errors=(), steps=(candidate,))
    state = replace(RUNNING_STATE, last_cycle=NOW)
    assigned = Task(candidate.bead_id, "quality-mgr", "active", 0, ACTION.at, ACTION.at, "running")
    due_calls = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(phase))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok((assigned,)))

    def due(current_state, current_phase, prefix, github, now, tasks=()):
        decision = model_due(current_state, current_phase, prefix, github, now, tasks)
        due_calls.append((current_state, current_phase, prefix, github, now, tasks, decision))
        return decision

    def forbidden(*_args, **_kwargs):
        raise AssertionError("assigned ready work must not trigger this due read")

    monkeypatch.setattr(runner.model, "due", due)
    monkeypatch.setattr(runner.git_source, "fetch", forbidden)
    monkeypatch.setattr(runner.git_source, "remote_refs", forbidden)
    monkeypatch.setattr(runner.git_source, "merge", forbidden)

    without_task = model_due(state, phase, REPO_CONFIG.bead_prefix, CONFIG.github, NOW)
    assert without_task.fetch and without_task.merges == ((candidate.branch, candidate.target),)

    runner.step(replace(CONFIG, no_fetch=False), state, False, store)

    assert due_calls == [(state, phase, REPO_CONFIG.bead_prefix, CONFIG.github, NOW, (assigned,), Due(False, (), False))]


def test_forced_github_timeout_is_visible_and_logged_once(github_runtime) -> None:
    store, calls, events, inputs = github_runtime

    snapshot, _ = runner.step(CONFIG, STATE, True, store)

    assert calls == ["rest"]
    assert inputs[0].prs == Err(TIMEOUT)
    assert snapshot.gh.error == TIMEOUT and snapshot.errors == (TIMEOUT,)
    assert [event for event in events if event[0] == "github_read"] == [
        ("github_read", {"level": "info", "message": None, "forced": True,
                          "outcome": "failed", "error": asdict(TIMEOUT)})
    ]
    error_events = [fields for action, fields in events if action == "error"]
    assert len(error_events) == 1
    assert error_events[0]["level"] == "error"
    assert error_events[0]["message"] == "`retry the bd read` bd ready --json timeout after 20s"


def test_failed_github_refresh_retains_cached_prs_and_age(github_runtime, monkeypatch) -> None:
    store, _, events, inputs = github_runtime
    writes = []
    cached = replace(PRS_FILE, read_at=EARLIER)
    monkeypatch.setattr(store, "load_prs", lambda: Ok(cached))
    store.write_prs = lambda value: writes.append(value) or Ok(None)
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, step_errors=())))

    def tick(value, state, now, spec):
        inputs.append(value)
        return model_tick(value, state, now, spec)

    monkeypatch.setattr(runner.model, "tick", tick)
    snapshot, _ = runner.step(CONFIG, RUNNING_STATE, True, store)

    assert inputs[0].prs == Ok(cached.prs)
    assert tuple(row.pr for row in snapshot.prs) == cached.prs
    assert snapshot.gh == GhStatus(CONFIG.github, False, TIMEOUT, SNAPSHOT.generated_at, False)
    assert snapshot.gh.error == TIMEOUT and snapshot.errors == (TIMEOUT,)
    assert writes == [replace(cached, gh=snapshot.gh, attempted_at=snapshot.generated_at)]
    error_events = [fields for action, fields in events if action == "error"]
    assert len(error_events) == 1 and error_events[0]["error"] == asdict(TIMEOUT)


@pytest.mark.parametrize("interval_s", (120, 600))
def test_github_interval_is_independent_and_paused_refresh_keeps_cache(github_runtime, monkeypatch, interval_s):
    store, _, _, inputs = github_runtime
    clock = [NOW]
    cache = [replace(PRS_FILE, read_at=EARLIER, attempted_at=NOW,
                     gh=replace(GhStatus("rest", True, None, EARLIER, False),
                                core_remaining=4900, core_limit=5000, core_reset=NOW + timedelta(hours=1),
                                graphql_remaining=4800, graphql_limit=5000, graphql_reset=NOW + timedelta(hours=2),
                                quota_at=NOW))]
    calls = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0]

    def refresh(_config, now, gh, _groups):
        calls.append(now)
        if len(calls) == 1:
            return Err(TIMEOUT), replace(gh, paused=True, quota_remaining=2450, quota_limit=5000,
                                         quota_reset=NOW + timedelta(hours=1))
        return Ok(cache[0].prs), replace(gh, paused=False, error=None, ok=True)

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner, "_refresh_github", refresh)
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, step_errors=())))
    monkeypatch.setattr(store, "load_prs", lambda: Ok(cache[0]))
    monkeypatch.setattr(store, "write_prs", lambda value: cache.__setitem__(0, value) or Ok(None))
    config = replace(CONFIG, github_poll_interval_s=interval_s, dry_run=True)
    snapshot, _ = runner.step(config, RUNNING_STATE, False, store)
    assert calls == []
    assert (snapshot.gh.core_remaining, snapshot.gh.graphql_remaining, snapshot.gh.quota_at) == (4900, 4800, NOW)
    clock[0] += timedelta(seconds=interval_s)
    snapshot, _ = runner.step(config, RUNNING_STATE, False, store)
    assert calls == [clock[0]] and snapshot.gh.paused
    assert inputs[-1].prs == Ok(PRS_FILE.prs) and cache[0].prs == PRS_FILE.prs
    assert cache[0].read_at == EARLIER and cache[0].attempted_at == clock[0]
    clock[0] += timedelta(seconds=interval_s - 1)
    snapshot, _ = runner.step(config, RUNNING_STATE, False, store)
    assert len(calls) == 1
    assert (snapshot.gh.core_remaining, snapshot.gh.graphql_remaining, snapshot.gh.quota_at) == (4900, 4800, NOW)
    clock[0] += timedelta(seconds=1)
    snapshot, _ = runner.step(config, RUNNING_STATE, False, store)
    assert len(calls) == 2 and not snapshot.gh.paused


def test_paused_refresh_preserves_cached_availability_and_read_time(monkeypatch):
    cached = GhStatus("graphql", True, None, EARLIER, False)
    paused = replace(cached, ok=False, at=NOW, error=TIMEOUT, paused=True,
                     quota_remaining=2450, quota_limit=5000, quota_reset=NOW + timedelta(hours=1))
    monkeypatch.setattr(runner.github_source, "open_prs", lambda *_: (Err(TIMEOUT), paused))
    result, status = runner._refresh_github(replace(CONFIG, github="graphql"), NOW, cached)
    assert isinstance(result, Err)
    assert status.paused and status.ok is True and status.error is None and status.at == EARLIER


def test_github_off_skips_forced_read_and_event(github_runtime) -> None:
    store, calls, events, _ = github_runtime

    runner.step(replace(CONFIG, github="off"), STATE, True, store)

    assert calls == []
    assert not [event for event in events if event[0] == "github_read"]


@pytest.mark.parametrize("cached_mode", ("rest", "graphql"))
@pytest.mark.parametrize("force_github", (False, True))
def test_github_off_discards_populated_active_cache(github_runtime, monkeypatch, tmp_path, cached_mode, force_github) -> None:
    _, calls, events, _ = github_runtime
    class Clock:
        @staticmethod
        def now(_timezone):
            return SNAPSHOT.generated_at

    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, step_errors=())))
    monkeypatch.setattr(runner.model, "tick", model_tick)
    cached = replace(PRS_FILE, gh=GhStatus(cached_mode, True, None, SNAPSHOT.generated_at, False))
    acquired = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(acquired, Ok)
    config = replace(CONFIG, github="off", repo_root=str(tmp_path))

    with acquired.value as store:
        assert store.write_prs(cached) == Ok(None)
        assert load_prs(tmp_path, PHASE_FILE.root_id) == Ok(cached)

        snapshot, _ = runner.step(config, INITIAL_STATE, force_github, store)

    assert snapshot.gh == GhStatus("off", None, None, None, False)
    assert snapshot.prs == ()
    assert snapshot.top_pr is None and snapshot.top_ci is None
    assert load_snapshot(tmp_path, PHASE_FILE.root_id) == Ok(snapshot)
    persisted = load_prs(tmp_path, PHASE_FILE.root_id)
    assert isinstance(persisted, Ok)
    assert persisted.value.gh == snapshot.gh and persisted.value.prs == ()
    assert calls == []
    assert not [event for event in events if event[0] == "github_read"]


def test_fetch_replans_cycle_merges_from_refreshed_remote_refs(github_runtime, monkeypatch) -> None:
    store, _, _, inputs = github_runtime
    fresh = RemoteRef("new-branch", REMOTE_REF.sha)
    due_states, merges = [], []

    def due(state, *_args):
        due_states.append(state.remotes)
        pairs = (("new-branch", "base"),) if fresh in state.remotes else ()
        return Due(True, pairs, False)

    monkeypatch.setattr(runner.model, "due", due)
    monkeypatch.setattr(runner.git_source, "fetch", lambda *_: Ok(None))
    monkeypatch.setattr(runner.git_source, "remote_refs", lambda *_: Ok((fresh,)))
    monkeypatch.setattr(runner.git_source, "merge", lambda *_args: merges.append(_args[1:]) or Ok(None))

    runner.step(replace(CONFIG, no_fetch=False), RUNNING_STATE, False, store)

    assert due_states == [RUNNING_STATE.remotes, (fresh,)]
    assert merges == [("new-branch", "base")]
    assert inputs[0].remotes == Ok((fresh,))


def test_failed_action_is_returned_for_snapshot_errors(monkeypatch) -> None:
    class Store:
        def append_action(self, _line):
            return Ok(None)

    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: Err(COMMAND_FAILED))
    logger = TickLog(runner.create_console_logger(CONFIG.repo_root))
    reported, errors = runner._run_actions(replace(CONFIG, dry_run=False), (ACTION,), Store(), ACTION.at, logger)
    assert errors == (COMMAND_FAILED,)
    assert reported == (replace(ACTION, outcome="failed command_failed"),)


@pytest.mark.parametrize(("sender", "sender_team"), (("atm-bd", "tool"), ("robot", "tooling")))
@pytest.mark.parametrize("delivery_fails", (False, True))
def test_lead_problem_uses_tool_sender_and_assignment_stays_with_lead(github_runtime, fake_bin, monkeypatch,
                                                                     sender, sender_team, delivery_fails):
    store, _, _, _ = github_runtime
    candidate = replace(PHASE.steps[0], status=SprintState.OPEN, ready=True, assignee=None, branch=None,
                        worktree=None, anchor=False, quick_fix=False)
    phase = replace(PHASE, closed=False, steps=(candidate,), step_errors=())
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(phase))
    monkeypatch.setattr(runner.model, "tick", model_tick)
    monkeypatch.setenv("ATM_IDENTITY", "unrelated")
    monkeypatch.setenv("ATM_TEAM", "unrelated")
    fake_bin.make("atm", f'if [ "$1" = send ]; then test "$ATM_IDENTITY" = "{sender}" && test "$ATM_TEAM" = "{sender_team}" || exit 23; '
                  + ('printf "delivery rejected" >&2; exit 3; ' if delivery_fails else 'cat >/dev/null; ')
                  + 'else test "$ATM_IDENTITY" = fenix && test "$ATM_TEAM" = atm-dev || exit 23; fi\n')
    config = replace(CONFIG, dry_run=False, notification_sender=f"{sender}@{sender_team}")

    snapshot, _ = runner.step(config, INITIAL_STATE, False, store)

    assert any(problem.to == "fenix" and problem.text == "missing branch configuration" for problem in snapshot.problems)
    calls = fake_bin.argv()
    sends = [argv for argv in calls if argv[1] == "send"]
    assert sends == [("atm", "send", "fenix@atm-dev", "--as", sender, "--team", sender_team, "--stdin")]
    assert all(f"{argv[argv.index('--as') + 1]}@{argv[argv.index('--team') + 1]}" != argv[2] for argv in sends)
    assert snapshot.sent[-1].outcome == ("failed command_failed" if delivery_fails else "sent")
    if delivery_fails:
        assert any(getattr(error, "stderr", "") == "delivery rejected" for error in snapshot.errors)

    assignment = replace(ACTION, outcome=None)
    reported, errors = runner._run_actions(config, (assignment,), store, ACTION.at,
                                           TickLog(runner.create_console_logger(config.repo_root)))
    assert errors == () and reported[0].outcome == "sent"
    argv = fake_bin.argv()[-1]
    assert argv[1:5] == ("task", "assign", "quality-mgr", "--as")
    assert argv[5:8] == ("fenix", "--team", "atm-dev")
    assert argv[3] != argv[5]


@pytest.mark.parametrize("failure", ("construction", "tick_start", "read", "github_read", "action", "tick_end", "flush"))
@pytest.mark.parametrize("delivery_fails", (False, True))
def test_logging_failure_is_persisted_without_changing_delivery(github_runtime, monkeypatch, failure, delivery_fails):
    store, _, _, _ = github_runtime
    snapshots = []
    store.write_snapshot = lambda snapshot: snapshots.append(snapshot) or Ok(None)
    logging_error = replace(IO_FAILED, path="logs", message=failure)

    class Logger:
        def event(self, action, **_fields):
            return Err(logging_error) if action == failure else Ok(None)

        def flush(self):
            return Err(logging_error) if failure == "flush" else Ok(None)

    created = Err(logging_error) if failure == "construction" else Ok(Logger())
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: created)
    monkeypatch.setattr(runner.model, "tick", lambda *_: (replace(SNAPSHOT, errors=()), (ACTION,), RUNNING_STATE))
    delivered = Err(COMMAND_FAILED) if delivery_fails else Ok(None)
    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: delivered)

    snapshot, next_state = runner.step(replace(CONFIG, dry_run=False), RUNNING_STATE, False, store)

    assert logging_error in snapshot.errors
    assert snapshots[-1].errors == snapshot.errors
    assert snapshot.sent[-1].outcome == ("failed command_failed" if delivery_fails else "sent")
    assert (COMMAND_FAILED in snapshot.errors) == delivery_fails
    assert next_state.sent == (RUNNING_STATE.sent if delivery_fails else (*RUNNING_STATE.sent, snapshot.sent[-1]))


@pytest.mark.parametrize("stopped", ("closed", "idle", "invalid_config"))
@pytest.mark.parametrize("failure", ("construction", "tick_start", "tick_end", "flush"))
def test_logging_failure_surfaces_on_early_return(github_runtime, monkeypatch, stopped, failure):
    store, _, _, _ = github_runtime
    logging_error = replace(IO_FAILED, path="logs", message=failure)
    recovery_error = replace(IO_FAILED, path="state.json", message="preserved corrupt state")
    write_error = replace(IO_FAILED, path="state.json", message="state write failed")
    writes = []
    store.load_errors = (recovery_error,)
    store.write_state = lambda value: writes.append(("state", value)) or Err(write_error)
    store.write_snapshot = lambda value: writes.append(("snapshot", value)) or Ok(None)

    class Logger:
        def event(self, action, **_fields):
            return Err(logging_error) if action == failure else Ok(None)

        def flush(self):
            return Err(logging_error) if failure == "flush" else Ok(None)

    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Err(logging_error) if failure == "construction" else Ok(Logger()))
    state = RUNNING_STATE
    if stopped == "closed":
        monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((replace(BEAD, status=SprintState.CLOSED),)))
    elif stopped == "idle":
        state = replace(state, idle_since=ACTION.at - timedelta(days=365))
    else:
        monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Err(TIMEOUT))

    result = runner.step(CONFIG, state, False, store)
    snapshot, _ = result

    assert logging_error in snapshot.errors
    assert result.reported == ()
    assert recovery_error in snapshot.errors and write_error in snapshot.errors
    assert [name for name, _ in writes] == ["state", "snapshot"]
    assert writes[-1][1] == snapshot


@pytest.mark.parametrize("kind", ("assign", "send", "batch"))
@pytest.mark.parametrize("outcome", ("sent", "failed command_failed", "dry_run"))
def test_every_effect_outcome_has_recipient_task_and_error_context(github_runtime, monkeypatch, kind, outcome):
    store, _, events, _ = github_runtime
    actions = tuple(replace(ACTION, kind=kind, bead_ids=(f"task-{index}", "related-task")) for index in range(7))
    monkeypatch.setattr(runner.model, "tick", lambda *_: (SNAPSHOT, actions, RUNNING_STATE))
    attempts = []

    def deliver(*args):
        attempts.append(args)
        return Err(COMMAND_FAILED) if outcome.startswith("failed") else Ok(None)

    monkeypatch.setattr(runner.atm_source, "assign", deliver)
    monkeypatch.setattr(runner.atm_source, "send", deliver)

    result = runner.step(replace(CONFIG, dry_run=outcome == "dry_run"), RUNNING_STATE, False, store)
    snapshot, _ = result

    effect_events = [fields for action, fields in events if action == "action"]
    assert len(effect_events) == len(actions)
    assert len(attempts) == (0 if outcome == "dry_run" else len(actions))
    for action, fields in zip(actions, effect_events):
        assert fields == {
            "effect": kind, "recipient": action.to, "task_ids": list(action.bead_ids),
            "outcome": outcome, "error": asdict(COMMAND_FAILED) if outcome.startswith("failed") else None,
            "level": "error" if outcome.startswith("failed") else "info",
            "message": error_message(asdict(COMMAND_FAILED)) if outcome.startswith("failed") else None,
        }
    assert all(action.outcome == outcome for action in snapshot.sent)
    assert result.reported == tuple(replace(action, outcome=outcome) for action in actions)
    assert len(snapshot.sent) == 5


def test_process_logger_is_reused_across_ticks(github_runtime, monkeypatch, tmp_path):
    store, _, _, _ = github_runtime
    created = []
    factory = log.create_logger

    def create(config):
        created.append(config)
        return factory(config)

    monkeypatch.setattr(log, "create_logger", create)
    monkeypatch.setattr(runner, "create_console_logger", log.create_console_logger)
    config = replace(CONFIG, repo_root=str(tmp_path))

    runner.step(config, RUNNING_STATE, False, store)
    runner.step(config, RUNNING_STATE, False, store)

    assert len(created) == 1
    logger = log.create_console_logger(tmp_path)
    assert isinstance(logger, Ok)
    queried = logger.value.query()
    assert isinstance(queried, Ok)
    assert [event.action for event in queried.value].count("tick_start") == 2
    assert [event.action for event in queried.value].count("tick_end") == 2


def test_fetch_and_merge_failures_are_logged_with_context(github_runtime, monkeypatch):
    store, _, events, _ = github_runtime
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(True, (("topic", "base"),), False))
    monkeypatch.setattr(runner.git_source, "fetch", lambda *_: Err(TIMEOUT))
    monkeypatch.setattr(runner.git_source, "merge", lambda *_: Err(COMMAND_FAILED))

    runner.step(replace(CONFIG, no_fetch=False), RUNNING_STATE, False, store)

    assert ("read", {"source": "git.fetch", "outcome": "failed", "error": asdict(TIMEOUT),
                     "level": "error", "message": error_message(asdict(TIMEOUT))}) in events
    assert not any(fields.get("source") == "git.merge" for _, fields in events)

    monkeypatch.setattr(runner.git_source, "fetch", lambda *_: Ok(None))
    monkeypatch.setattr(runner.git_source, "remote_refs", lambda *_: Ok(()))
    runner.step(replace(CONFIG, no_fetch=False), RUNNING_STATE, False, store)

    assert ("read", {"source": "git.merge", "outcome": "failed", "error": asdict(COMMAND_FAILED),
                     "level": "error", "message": error_message(asdict(COMMAND_FAILED)),
                     "branch": "topic", "target": "base"}) in events


def test_source_failure_and_dispatch_hold_are_logged(github_runtime, monkeypatch):
    store, _, events, _ = github_runtime
    held_step = PHASE.steps[0]
    held = SectionRow(held_step.bead_id, 0, "branch not on origin", None)
    snapshot = replace(SNAPSHOT, sections=(Section("Held", (held,)),))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Err(TIMEOUT))
    monkeypatch.setattr(runner.model, "tick", lambda *_: (snapshot, (), RUNNING_STATE))

    runner.step(CONFIG, RUNNING_STATE, False, store)

    assert ("read", {"source": "atm.members", "outcome": "failed", "error": asdict(TIMEOUT),
                     "level": "error", "message": error_message(asdict(TIMEOUT)),
                     "team": CONFIG.team}) in events
    assert ("hold", {"task_ids": [held.bead_id], "recipient": held_step.assignee or CONFIG.lead,
                     "level": "info", "message": None,
                     "outcome": "held", "reason": held.reason}) in events


def test_failed_notice_is_not_committed_and_remains_pending() -> None:
    action = replace(ACTION, kind="send", to="fenix", bead_ids=("atm-bh-6.fix",), spec=None)
    problem = Problem("missing_pr:branch", "atm-bh-6.fix", "fenix", "missing PR", ACTION.at)
    proposed = replace(STATE, problems=(problem,), cycle_first_send=ACTION.at, sent=(action,), batch_due={"fenix": ACTION.at})

    durable = runner._durable_state(STATE, proposed, (action,), (replace(action, outcome="failed command_failed"),))

    assert durable.problems == () and durable.sent == STATE.sent and durable.batch_due == {}
    assert durable.cycle_first_send == STATE.cycle_first_send


def test_dry_run_reports_actions_without_advancing_durable_state(github_runtime, monkeypatch) -> None:
    store, _, _, _ = github_runtime
    writes = []
    store.write_state = lambda value: writes.append(value) or Ok(None)
    candidate = replace(RUNNING_STATE, last_cycle=ACTION.at, sent=(ACTION,))
    monkeypatch.setattr(runner.model, "tick", lambda _inputs, _state, _now, _spec: (SNAPSHOT, (ACTION,), candidate))

    snapshot, next_state = runner.step(CONFIG, RUNNING_STATE, False, store)

    assert next_state == RUNNING_STATE and writes == [RUNNING_STATE]
    assert snapshot.sent == (*RUNNING_STATE.sent, replace(ACTION, outcome="dry_run"))
    sent = []
    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: sent.append(ACTION) or Ok(None))
    _, live_state = runner.step(replace(CONFIG, dry_run=False), next_state, False, store)
    assert sent == [ACTION] and live_state.sent[-1].outcome == "sent"


def test_failed_delivery_is_retried_on_the_next_tick(github_runtime, monkeypatch) -> None:
    store, _, _, _ = github_runtime
    attempts = []
    action = replace(ACTION, kind="send", to="fenix", bead_ids=("atm-bh-6.fix",), spec=None)
    problem = Problem("missing_pr:branch", "atm-bh-6.fix", "fenix", "missing PR", ACTION.at)

    def tick(_inputs, state, now, _spec):
        candidate = replace(state, problems=(problem,), cycle_first_send=now, sent=(*state.sent, action)[-5:], batch_due={"fenix": now})
        return SNAPSHOT, (action,), candidate

    monkeypatch.setattr(runner.model, "tick", tick)
    monkeypatch.setattr(runner.atm_source, "send", lambda *_: attempts.append(1) or Err(COMMAND_FAILED))

    _, after_first = runner.step(replace(CONFIG, dry_run=False), INITIAL_STATE, False, store)
    _, after_second = runner.step(replace(CONFIG, dry_run=False), after_first, False, store)

    assert attempts == [1, 1]
    assert after_first.problems == after_second.problems == ()


def test_second_atm_action_failure_is_persisted(github_runtime, monkeypatch) -> None:
    store, _, _, _ = github_runtime
    snapshots = []
    store.write_snapshot = lambda value: snapshots.append(value) or Ok(None)
    monkeypatch.setattr(runner.model, "tick", lambda _inputs, state, _now, _spec: (SNAPSHOT, (ACTION, ACTION), state))
    outcomes = iter((Ok(None), Err(COMMAND_FAILED)))
    monkeypatch.setattr(runner.atm_source, "send", lambda *_: next(outcomes))
    snapshot, _ = runner.step(CONFIG, RUNNING_STATE, False, store)
    assert COMMAND_FAILED in snapshot.errors
    assert snapshots[-1].errors == snapshot.errors


def test_stubbed_write_snapshot_failure_is_visible(github_runtime) -> None:
    store, _, _, _ = github_runtime
    store.write_snapshot = lambda _value: Err(IO_FAILED)
    snapshot, _ = runner.step(CONFIG, RUNNING_STATE, False, store)
    assert IO_FAILED in snapshot.errors


@pytest.mark.parametrize("path", ("normal", "closed", "invalid_config"))
@pytest.mark.parametrize("logging_fails", (False, True))
def test_snapshot_write_failure_is_logged_and_flushed_once(github_runtime, monkeypatch, path, logging_fails):
    store, _, _, _ = github_runtime
    snapshot_error = replace(IO_FAILED, path="snapshot.json", message="snapshot write failed")
    admission_error = replace(IO_FAILED, path="logs", message="error admission failed")
    flush_error = replace(IO_FAILED, path="logs", message="error flush failed")
    events, flushes, writes = [], [], []

    class Logger:
        def event(self, action, **fields):
            events.append((action, fields))
            return Err(admission_error) if logging_fails and fields.get("source") == "snapshot.write" else Ok(None)

        def flush(self):
            flushes.append(len(events))
            return Err(flush_error) if logging_fails and len(flushes) == 2 else Ok(None)

    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    store.write_snapshot = lambda value: writes.append(value) or Err(snapshot_error)
    if path == "closed":
        monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((replace(BEAD, status=SprintState.CLOSED),)))
    elif path == "invalid_config":
        monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Err(TIMEOUT))

    snapshot, _ = runner.step(CONFIG, RUNNING_STATE, False, store)

    assert len(writes) == 1
    assert [action for action, _ in events].count("tick_end") == 1
    assert events[-1] == ("error", {
        "source": "snapshot.write", "level": "error",
        "message": "`repair permissions` io_failed snapshot write failed",
        "error": asdict(snapshot_error),
    })
    assert flushes[-1] == len(events) and len(flushes) == 2
    assert snapshot.errors == (*writes[0].errors, snapshot_error, *((admission_error, flush_error) if logging_fails else ()))


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="chmod permissions require a non-root POSIX user")
def test_runner_reports_real_read_only_store_write_failure(github_runtime, tmp_path) -> None:
    _, _, _, _ = github_runtime
    acquired = acquire_lock(tmp_path, "atm-phase-bh")
    assert isinstance(acquired, Ok)
    directory = acquired.value.directory
    with acquired.value as store:
        directory.chmod(0o500)
        try:
            snapshot, _ = runner.step(replace(CONFIG, repo_root=str(tmp_path)), RUNNING_STATE, False, store)
        finally:
            directory.chmod(0o700)
    assert any(isinstance(error, IoFailed) and error.path == str(directory / "snapshot.json") for error in snapshot.errors)


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
    assert ("atm", "task", "list", "--all", "--json", "--team", "atm-dev", "--as", "fenix") in argv
    assert all(command[command.index("--team") + 1] != "fenix" for command in argv if "--team" in command)


@pytest.mark.parametrize("failed_source", ("phase_file", "repo_config"))
def test_config_error_mid_run_replaces_persisted_snapshot(monkeypatch, tmp_path, failed_source) -> None:
    class Clock:
        @staticmethod
        def now(_timezone): return SNAPSHOT.generated_at

    monkeypatch.setattr(runner, "datetime", Clock)
    error = ConfigInvalid("config_invalid", failed_source, "bad", "repair the configuration")
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.atm_bd, failed_source, lambda *_: Err(error))
    config = replace(CONFIG, repo_root=str(tmp_path))
    acquired = acquire_lock(tmp_path, PHASE_FILE.root_id)
    assert isinstance(acquired, Ok)

    with acquired.value as store:
        assert store.write_state(STATE) == Ok(None)
        assert store.write_snapshot(replace(SNAPSHOT, errors=())) == Ok(None)

        snapshot, next_state = runner.step(config, STATE, False, store)

        assert snapshot.errors == (error,)
        assert snapshot.files == {
            "snapshot": str(store.directory / "snapshot.json"),
            "state": str(store.directory / "state.json"),
        }
        assert load_snapshot(tmp_path, PHASE_FILE.root_id) == Ok(snapshot)
        assert load_state(tmp_path, PHASE_FILE.root_id) == Ok(STATE)
        assert next_state == STATE


@pytest.mark.parametrize("failed_writes", (("state",), ("snapshot",), ("state", "snapshot")))
def test_config_error_reports_write_failures(monkeypatch, tmp_path, failed_writes) -> None:
    writes = []

    class Store:
        directory = tmp_path
        load_errors = ()

        def write_state(self, value):
            writes.append(("state", value))
            return Err(IO_FAILED) if "state" in failed_writes else Ok(None)

        def write_snapshot(self, value):
            writes.append(("snapshot", value))
            return Err(IO_FAILED) if "snapshot" in failed_writes else Ok(None)

    error = ConfigInvalid("config_invalid", "phase", "bad", "repair the phase file")
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Err(error))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))

    snapshot, next_state = runner.step(CONFIG, STATE, False, Store())

    assert [name for name, _ in writes] == ["state", "snapshot"]
    assert writes[0][1] == STATE and next_state == STATE
    assert writes[1][1].errors == (error, *((IO_FAILED,) if "state" in failed_writes else ()))
    assert snapshot.errors == (error, *(IO_FAILED for _ in failed_writes))
