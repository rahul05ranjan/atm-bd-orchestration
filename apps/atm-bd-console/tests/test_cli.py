from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from contract_samples import SNAPSHOT

from atm_bd_console import cli
from atm_bd_console.result import Ok


def test_missing_as_uses_documented_message(capsys) -> None:
    assert cli.main(["--phase", "bh"]) == 3
    assert capsys.readouterr().out.strip() == cli.MISSING_AS


def test_show_reads_snapshot_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(replace(SNAPSHOT, generated_at=datetime.now(UTC))))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out
