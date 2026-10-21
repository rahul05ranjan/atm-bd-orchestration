"""Closed beads never change: the runner and the Quality loader read each one once per console process."""
from __future__ import annotations

from dataclasses import replace

import pytest
from contract_samples import CONFIG, NOW, PHASE, PHASE_FILE, REPO_CONFIG, SNAPSHOT, TIMEOUT
from test_runner import RUNNING_STATE, Store, fake_store

from atm_bd_console import atm_bd, beads_source, ledger_source, runner
from atm_bd_console.closed_cache import ClosedBeads
from atm_bd_console.enums import SprintState
from atm_bd_console.model import Due
from atm_bd_console.result import Err, IoFailed, Ok, ParseFailed
from atm_bd_console.types import Bead

ROOT = PHASE_FILE.root_id
SPRINT_IDS = tuple(i for sprint in PHASE_FILE.sprints for i in (sprint, *atm_bd.group_ids(sprint)))


def bead(bead_id: str, status: SprintState = SprintState.CLOSED) -> Bead:
    return Bead(bead_id, bead_id, status, ("phase:bh",), ROOT, (), None, {}, NOW, None, NOW if status == SprintState.CLOSED else None)


class FakeBd:
    """Recording stand-in for the bd source; `world` is what bd would return, `fail` makes reads fail."""

    def __init__(self) -> None:
        self.world = {ROOT: bead(ROOT, SprintState.OPEN), **{i: bead(i) for i in SPRINT_IDS}}
        self.shows: list[tuple[str, ...]] = []
        self.opened: tuple[Bead, ...] = ()
        self.fail = False

    def show(self, ids):
        self.shows.append(tuple(ids))
        if self.fail:
            return Err(TIMEOUT)
        return Ok(tuple(self.world[i] for i in ids if i in self.world))

    def closed_reads(self) -> list[tuple[str, ...]]:
        return [ids for ids in self.shows if ids and ids != (ROOT,)]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeBd:
    bd = FakeBd()

    class Logger:
        def event(self, *_args, **_fields): return Ok(None)
        def flush(self): return Ok(None)

    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", bd.show)
    monkeypatch.setattr(runner.beads_source, "open_rows", lambda *_: Ok(beads_source.BeadRows(bd.opened, ())))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(PHASE))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(False, (), True))
    monkeypatch.setattr(runner.model, "tick", lambda value, state, *_: (replace(SNAPSHOT, errors=()), (), state))
    return bd


def tick(cache: ClosedBeads):
    return runner.step(replace(CONFIG, github="off", no_fetch=True, dry_run=True), RUNNING_STATE, False,
                       fake_store(Store()), cache)


def test_second_tick_issues_no_closed_reads(fake):
    cache = ClosedBeads()
    tick(cache)
    assert fake.closed_reads() == [SPRINT_IDS]
    tick(cache)
    assert fake.closed_reads() == [SPRINT_IDS]  # unchanged: nothing was read for them on tick 2


def test_open_bead_is_reread_every_tick(fake):
    fake.world["atm-bh-1"] = bead("atm-bh-1", SprintState.OPEN)  # bd returns it open, outside the open list
    cache = ClosedBeads()
    tick(cache)
    tick(cache)
    assert [ids for ids in fake.closed_reads()] == [SPRINT_IDS, ("atm-bh-1",)]


def test_open_then_closed_bead_enters_cache(fake):
    fake.opened = (bead("atm-bh-1", SprintState.IN_PROGRESS),)
    cache = ClosedBeads()
    tick(cache)
    assert "atm-bh-1" not in fake.closed_reads()[0]
    fake.opened = ()
    tick(cache)
    assert fake.closed_reads()[1] == ("atm-bh-1",)
    tick(cache)
    assert len(fake.closed_reads()) == 2


def test_failed_read_is_the_original_err_and_caches_nothing(fake, monkeypatch):
    seen = []
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *args: seen.append(args) or Ok(PHASE))
    cache = ClosedBeads()
    fake.fail = True
    tick(cache)
    assert cache.missing_beads(SPRINT_IDS) == SPRINT_IDS
    assert not seen  # no phase was built from the failed read
    fake.fail = False
    tick(cache)
    assert fake.closed_reads()[-1] == SPRINT_IDS


def test_show_closed_returns_the_original_err(fake):
    fake.fail = True
    from atm_bd_console.log import TickLog
    logger = TickLog(runner.create_console_logger("."))
    assert runner._show_closed(ClosedBeads(), SPRINT_IDS, logger) == Err(TIMEOUT)


def test_partial_result_caches_only_returned_beads(fake):
    present = SPRINT_IDS[:3]
    for missing in SPRINT_IDS[3:]:
        del fake.world[missing]
    cache = ClosedBeads()
    tick(cache)
    assert cache.missing_beads(SPRINT_IDS) == SPRINT_IDS[3:]
    fake.world.update({i: bead(i) for i in SPRINT_IDS[3:]})
    tick(cache)
    assert fake.closed_reads()[1] == SPRINT_IDS[3:]
    assert present and cache.missing_beads(SPRINT_IDS) == ()


def test_reopened_bead_is_forgotten(fake):
    cache = ClosedBeads()
    tick(cache)
    fake.opened = (bead("atm-bh-1", SprintState.OPEN),)
    tick(cache)
    assert "atm-bh-1" in cache.missing_beads(SPRINT_IDS)


# Quality tab: closed stage lists and checked beads.

def qa_row(bead_id: str, checked: str, status: str = "closed") -> dict:
    return {"id": bead_id, "status": status, "labels": ["phase-bh", "stage:qa"], "metadata": {"checked_bead": checked}}


class FakeStages:
    def __init__(self, monkeypatch) -> None:
        self.lists: list[str] = []
        self.shows: list[tuple[str, ...]] = []
        self.closed = {"qa": [qa_row("qa-1", "dev-1")], "finding": []}
        self.rows = {"dev-1": {"id": "dev-1", "status": "closed"}, "qa-2": qa_row("qa-2", "dev-2"),
                     "dev-2": {"id": "dev-2", "status": "closed"}}
        self.fail = False
        monkeypatch.setattr(beads_source, "closed_stage_rows", self.stage)
        monkeypatch.setattr(beads_source, "show_rows", self.show)

    def stage(self, _phase, stage, *, cwd=None):
        self.lists.append(stage)
        return Err(TIMEOUT) if self.fail else Ok(list(self.closed[stage]))

    def show(self, ids, *, cwd=None):
        self.shows.append(tuple(ids))
        return Err(TIMEOUT) if self.fail else Ok([self.rows[i] for i in ids if i in self.rows])


def test_closed_quality_reads_once_then_serves_from_cache(monkeypatch):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads()
    first = ledger_source.closed_quality(".", "bh", cache)
    second = ledger_source.closed_quality(".", "bh", cache)
    assert isinstance(first, Ok) and first == second
    assert fake.lists == ["qa", "finding"] and fake.shows == [("dev-1",)]


def test_closed_quality_failure_is_original_err_and_caches_nothing(monkeypatch):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads()
    fake.fail = True
    assert ledger_source.closed_quality(".", "bh", cache) == Err(TIMEOUT)
    assert cache.stage_rows("bh", "qa") is None
    fake.fail = False
    assert isinstance(ledger_source.closed_quality(".", "bh", cache), Ok)
    assert fake.lists == ["qa", "qa", "finding"]


def test_checked_bead_failure_caches_no_rows(monkeypatch):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads()
    original = fake.show
    monkeypatch.setattr(beads_source, "show_rows", lambda ids, cwd=None: Err(TIMEOUT))
    assert ledger_source.closed_quality(".", "bh", cache) == Err(TIMEOUT)
    assert cache.missing_rows(("dev-1",)) == ("dev-1",)
    monkeypatch.setattr(beads_source, "show_rows", original)
    assert isinstance(ledger_source.closed_quality(".", "bh", cache), Ok)


def test_bead_closing_after_first_load_is_read_once(monkeypatch):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads()
    cache.note_open("bh", [qa_row("qa-2", "dev-2", "open")])
    ledger_source.closed_quality(".", "bh", cache)
    cache.note_open("bh", [])  # qa-2 left the open list: it closed
    result = ledger_source.closed_quality(".", "bh", cache)
    assert isinstance(result, Ok) and [run.bead["id"] for run in result.value[0]] == ["qa-1", "qa-2"]
    assert fake.lists == ["qa", "finding"] and fake.shows == [("dev-1",), ("qa-2",), ("dev-2",)]
    ledger_source.closed_quality(".", "bh", cache)
    assert len(fake.shows) == 3


def test_refresh_relists_stages_but_keeps_closed_rows(monkeypatch):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads()
    ledger_source.closed_quality(".", "bh", cache)
    cache.drop_stage_lists()
    ledger_source.closed_quality(".", "bh", cache)
    assert fake.lists == ["qa", "finding", "qa", "finding"] and fake.shows == [("dev-1",)]


# Persistence across a restart.

def test_first_tick_after_restart_reads_no_closed_beads(fake, tmp_path):
    tick(ClosedBeads(tmp_path))
    assert fake.closed_reads() == [SPRINT_IDS]
    restarted = ClosedBeads.load(tmp_path)
    assert restarted.pending_errors == []
    tick(restarted)
    assert fake.closed_reads() == [SPRINT_IDS]  # the warm first tick issued none


def test_open_bead_is_not_persisted(fake, tmp_path):
    fake.world["atm-bh-1"] = bead("atm-bh-1", SprintState.OPEN)
    tick(ClosedBeads(tmp_path))
    restarted = ClosedBeads.load(tmp_path)
    assert restarted.missing_beads(SPRINT_IDS) == ("atm-bh-1",)


def test_failed_read_persists_nothing(fake, tmp_path):
    fake.fail = True
    tick(ClosedBeads(tmp_path))
    assert not (tmp_path / "closed.json").exists()


@pytest.mark.parametrize("content", ["{not json", '{"beads": 3}', '{"beads":[],"rows":[],"stage_rows":[],"seen_open":[],"extra":1}'])
def test_corrupt_file_is_a_typed_error_and_the_console_starts_cold(fake, tmp_path, content):
    (tmp_path / "closed.json").write_text(content, encoding="utf-8")
    cache = ClosedBeads.load(tmp_path)
    assert len(cache.pending_errors) == 1 and isinstance(cache.pending_errors[0], ParseFailed)
    assert str(tmp_path / "closed.json") in cache.pending_errors[0].source
    snapshot, _ = tick(cache)
    assert cache.pending_errors == [] and cache.pending_errors is not None
    assert any(isinstance(error, ParseFailed) for error in snapshot.errors)
    assert fake.closed_reads() == [SPRINT_IDS]  # cold read
    assert ClosedBeads.load(tmp_path).missing_beads(SPRINT_IDS) == ()  # the next save replaced the corrupt file


def test_unreadable_file_is_a_typed_io_error(tmp_path):
    (tmp_path / "closed.json").mkdir()
    cache = ClosedBeads.load(tmp_path)
    assert len(cache.pending_errors) == 1 and isinstance(cache.pending_errors[0], IoFailed)


def test_write_failure_is_a_typed_error_and_retried(fake, tmp_path):
    cache = ClosedBeads(tmp_path / "missing")
    snapshot, _ = tick(cache)
    assert any(isinstance(error, IoFailed) for error in snapshot.errors)
    assert cache.missing_beads(SPRINT_IDS) == ()  # the in-memory cache still works
    (tmp_path / "missing").mkdir()
    assert cache.save() == Ok(None)
    assert ClosedBeads.load(tmp_path / "missing").missing_beads(SPRINT_IDS) == ()


def test_quality_stage_lists_survive_a_restart(monkeypatch, tmp_path):
    fake = FakeStages(monkeypatch)
    cache = ClosedBeads(tmp_path)
    cache.note_open("bh", [qa_row("qa-2", "dev-2", "open")])
    ledger_source.closed_quality(".", "bh", cache)
    assert cache.save() == Ok(None)
    restarted = ClosedBeads.load(tmp_path)
    restarted.note_open("bh", [])  # qa-2 closed while the console was down
    result = ledger_source.closed_quality(".", "bh", restarted)
    assert isinstance(result, Ok) and [run.bead["id"] for run in result.value[0]] == ["qa-1", "qa-2"]
    assert fake.lists == ["qa", "finding"]  # no list after the restart
    assert fake.shows == [("dev-1",), ("qa-2",), ("dev-2",)]


def test_forget_beads_evicts_the_closed_rows_and_stage_rows_too() -> None:
    cache = ClosedBeads()
    row = {"id": "qa-1", "status": "closed"}
    cache.store_beads([bead("qa-1")])
    cache.set_stage_rows("phase-bh", "qa", [row, {"id": "qa-2", "status": "closed"}])

    cache.forget_beads(["qa-1"])

    assert cache.missing_rows(["qa-1"]) == ("qa-1",)
    assert cache.cached_rows(["qa-1"]) == []
    assert [r["id"] for r in cache.stage_rows("phase-bh", "qa") or []] == ["qa-2"]
