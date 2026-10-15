from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime

from contract_samples import SNAPSHOT

from atm_bd_console import cli
from atm_bd_console.result import Err, NotFound, Ok


def test_missing_as_uses_documented_message(capsys) -> None:
    assert cli.main(["--phase", "bh"]) == 3
    assert capsys.readouterr().out.strip() == cli.MISSING_AS


def test_missing_phase_is_a_launch_check(capsys) -> None:
    assert cli.main([]) == 3
    assert "--phase <phase> is required" in capsys.readouterr().out


def test_cli_import_does_not_load_textual() -> None:
    check = subprocess.run(
        [sys.executable, "-c", "import sys; import atm_bd_console.cli; assert 'textual' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert check.returncode == 0, check.stderr


def test_show_reads_snapshot_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(replace(SNAPSHOT, generated_at=datetime.now(UTC))))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_show_stopped_idle_skips_staleness(monkeypatch, capsys) -> None:
    stale_idle = replace(SNAPSHOT, stopped="idle", generated_at=SNAPSHOT.generated_at.replace(year=2000))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(stale_idle))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: idle" in capsys.readouterr().out


def test_show_stale_and_missing_snapshot_report_not_running(monkeypatch, capsys) -> None:
    stale = replace(SNAPSHOT, stopped=None, generated_at=SNAPSHOT.generated_at.replace(year=2000))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(stale))
    assert cli.main(["show", "--phase", "bh"]) == 2
    assert "last tick" in capsys.readouterr().out
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Err(NotFound("not_found", "snapshot", "run a console tick")))
    assert cli.main(["show", "--phase", "bh"]) == 2
    assert "is not running" in capsys.readouterr().out
