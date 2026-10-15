from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime

from contract_samples import CONFIG, PHASE_FILE, REPO_CONFIG, SNAPSHOT, TIMEOUT

from atm_bd_console import cli
from atm_bd_console.result import Err, NotFound, Ok


def test_missing_as_uses_documented_message(capsys) -> None:
    assert cli.main(["--phase", "bh"]) == 3
    assert capsys.readouterr().out.strip() == cli.MISSING_AS


def test_missing_phase_is_a_launch_check(capsys) -> None:
    assert cli.main([]) == 3
    assert "--phase <phase> is required" in capsys.readouterr().out


def test_headless_and_show_do_not_load_textual(tmp_path) -> None:
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from atm_bd_console.cli import main; "
                "main(['--phase', 'x', '--as', 'y', '--headless']); "
                "main(['show', '--phase', 'x']); "
                "assert 'textual' not in sys.modules"
            ),
        ],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    assert check.returncode == 0, check.stderr


def test_show_reads_snapshot_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(replace(SNAPSHOT, generated_at=datetime.now(UTC))))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_show_uses_the_configured_repository_root(monkeypatch, capsys, tmp_path) -> None:
    root = tmp_path / "repository"
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: root)
    monkeypatch.setattr(cli, "load_snapshot", lambda actual_root, *_: Ok(SNAPSHOT) if actual_root == root else (_ for _ in ()).throw(AssertionError("wrong root")))
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


def test_headless_stopped_snapshot_and_errors(monkeypatch, capsys) -> None:
    class Lock:
        def __enter__(self): return self
        def __exit__(self, *_): return None

    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "load_state", lambda *_: Err(NotFound("not_found", "state", "run")))
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Ok(Lock()))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped="idle"), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped=None, errors=(TIMEOUT,)), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out
