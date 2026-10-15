from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from atm_bd_console import state
from atm_bd_console.codec import from_json
from atm_bd_console.result import Err, IoFailed, LockHeld, NotFound, Ok
from atm_bd_console.state import acquire_lock, load_merges, load_prs, load_snapshot, load_state
from atm_bd_console.types import ActionLine, MergeFile, PrsFile, Snapshot, State

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def fixture(name: str, cls):
    decoded = from_json(cls, (FIXTURES / name).read_text())
    assert isinstance(decoded, Ok)
    return decoded.value


def test_detail_files_are_atomic_round_trips_and_actions_are_lines(tmp_path: Path) -> None:
    snapshot = fixture("snapshot-running.json", Snapshot)
    state = State((), None, None, None, None, (), (), {})
    merges = fixture("merge.json", MergeFile)
    prs = fixture("prs.json", PrsFile)
    acquired = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(acquired, Ok)
    with acquired.value as store:
        assert store.write_snapshot(snapshot) == Ok(None)
        assert store.write_state(state) == Ok(None)
        assert store.write_merges(merges) == Ok(None)
        assert store.write_prs(prs) == Ok(None)
        action = ActionLine(datetime.now(UTC), "hold", "fenix", "atm-bh", "wait")
        assert store.append_action(action) == Ok(None)
        assert store.append_action(action) == Ok(None)
    assert load_snapshot(tmp_path, "atm-bh") == Ok(snapshot)
    assert load_state(tmp_path, "atm-bh") == Ok(state)
    assert load_merges(tmp_path, "atm-bh") == Ok(merges)
    assert load_prs(tmp_path, "atm-bh") == Ok(prs)
    lines = (tmp_path / ".atm-bd/atm-bd-console/atm-bh/actions.jsonl").read_text().splitlines()
    assert len(lines) == 2 and all(isinstance(from_json(ActionLine, line), Ok) for line in lines)
    assert (tmp_path / ".atm-bd/atm-bd-console/.gitignore").read_text() == "*\n"


def test_written_files_store_datetimes_only_in_utc_z_form(tmp_path: Path) -> None:
    acquired = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(acquired, Ok)
    with acquired.value as store:
        assert store.write_snapshot(fixture("snapshot-running.json", Snapshot)) == Ok(None)
        assert store.append_action(ActionLine(datetime.now(UTC), "hold", "fenix", "atm-bh", "wait")) == Ok(None)
    directory = tmp_path / ".atm-bd/atm-bd-console/atm-bh"
    text = (directory / "snapshot.json").read_text() + (directory / "actions.jsonl").read_text()
    stamps = re.findall(r'"(\d{4}-\d{2}-\d{2}T[^"]*)"', text)
    assert stamps and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp) for stamp in stamps)


def test_read_only_state_directory_returns_io_failed_naming_the_path(tmp_path: Path) -> None:
    acquired = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(acquired, Ok)
    directory = tmp_path / ".atm-bd/atm-bd-console/atm-bh"
    snapshot = fixture("snapshot-running.json", Snapshot)
    action = ActionLine(datetime.now(UTC), "hold", "fenix", "atm-bh", "wait")
    with acquired.value as store:
        directory.chmod(0o500)
        try:
            written = store.write_snapshot(snapshot)
            appended = store.append_action(action)
        finally:
            directory.chmod(0o700)
    assert isinstance(written, Err) and isinstance(written.error, IoFailed)
    assert written.error.path == str(directory / "snapshot.json")
    assert isinstance(appended, Err) and isinstance(appended.error, IoFailed)
    assert appended.error.path == str(directory / "actions.jsonl")


def test_lock_held_reports_a_non_negative_snapshot_age_once_a_snapshot_exists(tmp_path: Path) -> None:
    first = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(first, Ok)
    with first.value as store:
        assert store.write_snapshot(fixture("snapshot-running.json", Snapshot)) == Ok(None)
        second = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(second, Err) and isinstance(second.error, LockHeld)
    assert isinstance(second.error.snapshot_age_s, int) and second.error.snapshot_age_s >= 0


def test_failed_write_leaves_the_previous_snapshot_intact(tmp_path: Path, monkeypatch) -> None:
    previous = fixture("snapshot-running.json", Snapshot)
    newer = replace(previous, phase="other")
    acquired = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(acquired, Ok)
    with acquired.value as store:
        assert store.write_snapshot(previous) == Ok(None)

        def fail(*_args, **_kwargs):
            raise OSError("disk full mid-write")

        monkeypatch.setattr(state.os, "replace", fail)
        result = store.write_snapshot(newer)
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert load_snapshot(tmp_path, "atm-bh") == Ok(previous)


def test_missing_data_and_live_process_lock_are_typed(tmp_path: Path) -> None:
    missing = load_snapshot(tmp_path, "atm-bh")
    assert isinstance(missing, Err) and isinstance(missing.error, NotFound)
    code = "from atm_bd_console.state import acquire_lock; import sys,time; held = acquire_lock(sys.argv[1], 'atm-bh'); print(type(held).__name__, flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], text=True, stdout=subprocess.PIPE)
    assert child.stdout is not None and child.stdout.readline().strip() == "Ok"
    held = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(held, Err) and isinstance(held.error, LockHeld) and held.error.snapshot_age_s is None
    child.kill()
    child.wait(timeout=5)
    released = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(released, Ok)
    released.value.close()


def test_interrupted_temp_file_does_not_replace_previous_snapshot(tmp_path: Path) -> None:
    snapshot = fixture("snapshot-running.json", Snapshot)
    acquired = acquire_lock(tmp_path, "atm-bh")
    assert isinstance(acquired, Ok)
    with acquired.value as store:
        assert store.write_snapshot(snapshot) == Ok(None)
        temporary = store.directory / ".snapshot.json.crashed"
        temporary.write_text("{not-json")
    assert load_snapshot(tmp_path, "atm-bh") == Ok(snapshot)
