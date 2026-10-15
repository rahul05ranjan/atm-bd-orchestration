from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from contract_samples import CONFIG, PHASE_FILE, REPO_CONFIG, SNAPSHOT, TIMEOUT

from atm_bd_console import __version__, app, cli
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import INITIAL_STATE

PKG = Path(__file__).resolve().parents[1]


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


def test_installed_cron_command_runs_one_headless_tick(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fixtures = PKG / "tests/fixtures/beads/repo"
    shutil.copytree(fixtures / ".atm-bd", repo / ".atm-bd")
    shutil.copytree(fixtures / "docs/plans", repo / "docs/plans")
    (repo / ".claude/project").mkdir(parents=True)
    shutil.copy2(fixtures / ".claude/project/atm-bd-orchestration.yaml", repo / ".claude/project/atm-bd-orchestration.yaml")
    (repo / ".atm.toml").write_text('[atm]\ndefault_team = "atm-dev"\n', encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("bd", "atm"):
        executable = fake_bin / name
        executable.write_text("#!/bin/sh\nprintf '[]\\n'\n", encoding="utf-8")
        executable.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(fake_bin), environment.get("PATH", "")))
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None, "uv is required to run the installed console entry point"

    help_result = subprocess.run(
        [uv, "run", "--project", str(PKG), "atm-bd-console", "--help"],
        capture_output=True, text=True, check=False, cwd=repo, env=environment,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "run one tick and exit" in help_result.stdout

    check = subprocess.run(
        [uv, "run", "--project", str(PKG), "atm-bd-console", "--headless", "--phase", "bh", "--as", "fenix", "--dry-run"],
        capture_output=True, text=True, check=False, cwd=repo, env=environment,
    )
    assert check.returncode == 0, check.stderr + check.stdout


def test_show_reads_snapshot_only(monkeypatch, capsys) -> None:
    clock = datetime(2026, 10, 15, tzinfo=UTC)
    monkeypatch.setattr(cli, "now", lambda: clock)
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(replace(SNAPSHOT, generated_at=clock)))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_version_and_non_headless_dispatch(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0 and __version__ in capsys.readouterr().out
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr("atm_bd_console.app.run", lambda config, step, state, startup_error, **_kwargs: 17)
    assert cli.main(["--phase", "bh", "--as", "fenix"]) == 17


@pytest.mark.parametrize("failure", ("config", "phase", "repo", "tool"))
def test_launch_checks_fail_before_running(monkeypatch, capsys, failure) -> None:
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Err(TIMEOUT) if failure == "config" else Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Err(TIMEOUT) if failure == "phase" else Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Err(TIMEOUT) if failure == "repo" else Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: None if failure == "tool" else "/fixture/bin")
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 3
    assert "not_found" in capsys.readouterr().out if failure == "tool" else "timeout" in capsys.readouterr().out


def test_show_uses_the_configured_repository_root(monkeypatch, capsys, tmp_path) -> None:
    root = tmp_path / "repository"
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: root)
    monkeypatch.setattr(cli, "load_snapshot", lambda actual_root, *_: Ok(SNAPSHOT) if actual_root == root else (_ for _ in ()).throw(AssertionError("wrong root")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_show_stopped_idle_skips_staleness(monkeypatch, capsys) -> None:
    clock = datetime(2026, 10, 15, tzinfo=UTC)
    monkeypatch.setattr(cli, "now", lambda: clock)
    stale_idle = replace(SNAPSHOT, stopped="idle", generated_at=clock.replace(year=2000))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(stale_idle))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: idle" in capsys.readouterr().out


def test_show_stale_and_missing_snapshot_report_not_running(monkeypatch, capsys) -> None:
    clock = datetime(2026, 10, 15, tzinfo=UTC)
    monkeypatch.setattr(cli, "now", lambda: clock)
    stale = replace(SNAPSHOT, stopped=None, generated_at=clock.replace(year=2000))
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
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped="closed"), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped=None, errors=(TIMEOUT,)), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out


def test_headless_lock_failure_is_a_json_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Err(TIMEOUT))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Err(NotFound("not_found", "snapshot", "run")))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out


def test_headless_real_lock_held_is_a_json_error(monkeypatch, capsys, tmp_path) -> None:
    config = replace(CONFIG, repo_root=str(tmp_path))
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(config))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    held = acquire_lock(tmp_path, "atm-phase-bh")
    assert isinstance(held, Ok)
    with held.value:
        assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out


def test_interactive_launch_passes_the_cli_loaded_state_to_the_app(monkeypatch) -> None:
    seen: list[tuple] = []
    kwargs = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "load_state", lambda *_: Err(TIMEOUT))
    monkeypatch.setattr(app, "run", lambda *args, **values: seen.append(args) or kwargs.append(values) or 0)

    assert cli.main(["--phase", "bh", "--as", "fenix"]) == 0
    assert seen == [(CONFIG, None, INITIAL_STATE, TIMEOUT)]
    assert kwargs == [{"runner_step": cli.runner.step}]
