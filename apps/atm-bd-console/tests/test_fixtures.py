from __future__ import annotations

import subprocess
from pathlib import Path

from atm_bd_console.codec import from_json
from atm_bd_console.result import Ok
from atm_bd_console.types import ActionLine, MergeFile, PrsFile, Snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def test_contract_fixtures_parse() -> None:
    types = {
        "merge.json": MergeFile,
        "prs.json": PrsFile,
        "snapshot-running.json": Snapshot,
        "snapshot-stopped-closed.json": Snapshot,
        "snapshot-stopped-idle.json": Snapshot,
        "snapshot-gh-off.json": Snapshot,
        "snapshot-gh-err.json": Snapshot,
    }
    for name, cls in types.items():
        assert isinstance(from_json(cls, (FIXTURES / name).read_text()), Ok), name
    assert isinstance(from_json(ActionLine, (FIXTURES / "actions.jsonl").read_text()), Ok)


def test_fake_bin_records_argv(fake_bin, monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    fake_bin("bd", "echo canned-output\nexit 0\n")
    completed = subprocess.run(["bd", "ready", "--json"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0
    assert log.read_text().splitlines() == ["ready", "--json"]
