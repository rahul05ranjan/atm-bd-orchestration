from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from conftest import SYSTEM_PATH, FakeBin
from contract_fixtures import SNAPSHOTS, fixtures

from atm_bd_console.codec import from_json, to_json
from atm_bd_console.result import Ok, Rejected
from atm_bd_console.types import ActionLine, Inputs, MergeFile, PrsFile, Snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def _load(name: str, cls: type) -> object:
    decoded = from_json(cls, (FIXTURES / name).read_text())
    assert isinstance(decoded, Ok), (name, decoded)
    return decoded.value


def test_committed_fixtures_are_the_to_json_output() -> None:
    expected = fixtures()
    assert sorted(path.name for path in FIXTURES.iterdir()) == sorted(expected)
    for name, text in expected.items():
        assert (FIXTURES / name).read_text() == text, f"{name}: run tests/contract_fixtures.py"


@pytest.mark.parametrize("name", sorted(SNAPSHOTS))
def test_snapshot_fixtures_are_populated(name: str) -> None:
    snapshot = _load(name, Snapshot)
    assert isinstance(snapshot, Snapshot)
    for field in ("agents", "sections", "problems", "sprints", "prs", "sent", "files", "errors"):
        assert len(getattr(snapshot, field)) > 0, f"{name}: {field} is empty"
    assert snapshot.agents[0].rows and snapshot.sections[0].rows and snapshot.sprints[0].steps
    assert snapshot.prs[0].merge is not None and snapshot.sent[0].spec is not None


def test_each_snapshot_shape_has_its_distinguishing_field() -> None:
    running = _load("snapshot-running.json", Snapshot)
    assert (running.stopped, running.root_closed, running.gh.mode, running.gh.ok) == (None, False, "graphql", True)
    closed = _load("snapshot-stopped-closed.json", Snapshot)
    assert (closed.stopped, closed.root_closed, closed.metrics.pct) == ("closed", True, 100)
    idle = _load("snapshot-stopped-idle.json", Snapshot)
    assert (idle.stopped, idle.root_closed) == ("idle", False)
    off = _load("snapshot-gh-off.json", Snapshot)
    assert (off.gh.mode, off.gh.ok, off.gh.at, off.top_ci) == ("off", None, None, None)
    err = _load("snapshot-gh-err.json", Snapshot)
    assert (err.gh.mode, err.gh.ok) == ("graphql", False)
    assert isinstance(err.gh.error, Rejected) and err.gh.error in err.errors
    shapes = [running, closed, idle, off, err]
    assert len({to_json(shape) for shape in shapes}) == len(shapes)
    assert running != off


def test_cache_fixtures_are_populated() -> None:
    inputs = _load("inputs.json", Inputs)
    assert isinstance(inputs.task_beads, Ok) and inputs.task_beads.value
    merge = _load("merge.json", MergeFile)
    assert {item.state for item in merge.merges.values()} == {"clean", "conflict"}
    assert all(key == item.branch for key, item in merge.merges.items())
    prs = _load("prs.json", PrsFile)
    assert [pr.ci for pr in prs.prs] == ["failure", "success"]
    assert prs.prs[0].failed_checks and prs.gh.ok is True


def test_actions_jsonl_has_one_line_per_kind_parsed_line_by_line() -> None:
    lines = (FIXTURES / "actions.jsonl").read_text().splitlines()
    parsed = [_load_line(line) for line in lines]
    assert [line.kind for line in parsed] == list(get_args(get_type_hints(ActionLine)["kind"]))
    dry_run = parsed[-1]
    assert dry_run.kind == "dry_run" and dry_run.to is None
    assert parsed[0].bead == "atm-bh-6.qa"


def _load_line(line: str) -> ActionLine:
    decoded = from_json(ActionLine, line)
    assert isinstance(decoded, Ok), (line, decoded)
    return decoded.value


def test_fake_bin_records_argv(fake_bin: FakeBin) -> None:
    assert os.environ["FAKE_ARGV_LOG"] == str(fake_bin.log)
    assert os.environ["PATH"].split(":") == [str(fake_bin.dir), *SYSTEM_PATH]
    fake_bin.make("bd", "echo canned-output\nexit 0\n")
    completed = subprocess.run(
        ["bd", "ready", "--json"], check=False, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0
    assert completed.stdout == "canned-output\n"
    assert fake_bin.argv() == [("bd", "ready", "--json")]


def test_fake_bin_keeps_invocations_and_awkward_arguments_apart(fake_bin: FakeBin) -> None:
    fake_bin.make("gh", "echo 'boom' >&2\nexit 3\n")
    first = subprocess.run(
        ["gh", "api", "two words", "line\nbreak"], check=False, capture_output=True, text=True, timeout=10,
        stdin=subprocess.DEVNULL,
    )
    second = subprocess.run(["gh"], check=False, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    assert (first.returncode, first.stderr, second.returncode) == (3, "boom\n", 3)
    assert fake_bin.argv() == [("gh", "api", "two words", "line\nbreak"), ("gh",)]
