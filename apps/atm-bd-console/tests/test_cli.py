from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from contract_samples import ACTION, BEAD, CONFIG, LOCK_HELD, PHASE, PHASE_FILE, REPO_CONFIG, SNAPSHOT

from atm_bd_console import cli, runner
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.state import load_snapshot, load_state


@pytest.fixture
def parity_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Freeze every runner boundary so cron and direct ticks have identical input."""
    class Clock:
        @staticmethod
        def now(_timezone):
            return SNAPSHOT.generated_at

    def tick(inputs, state, now, _spec):
        action = replace(ACTION, at=now)
        snapshot = replace(SNAPSHOT, generated_at=now, root_closed=False, stopped=None, errors=(), sent=(action,), files={})
        return snapshot, (action,), replace(state, last_cycle=now, sent=(action,))

    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((BEAD,)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(PHASE))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: None)
    monkeypatch.setattr(runner.model, "tick", tick)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")

    def config(name: str):
        root = tmp_path / name
        root.mkdir()
        return replace(CONFIG, repo_root=str(root), state_dir=str(root / ".atm-bd/atm-bd-console/atm-phase-bh"))

    return config


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


def test_once_headless_matches_two_direct_runner_ticks(parity_runtime, monkeypatch, capsys) -> None:
    """ANC-003: cron preserves both dispatch output and state over consecutive ticks."""
    direct = parity_runtime("direct")
    expected = []
    state = cli.EMPTY
    for _ in range(2):
        snapshot, state = runner.step(direct, state, False)
        expected.append((snapshot, state))

    cron = parity_runtime("cron")
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(cron))
    for expected_snapshot, expected_state in expected:
        assert cli.main(["--once", "--headless", "--phase", "bh", "--as", "fenix"]) == 0
        output = capsys.readouterr().out.strip()
        actual_snapshot = load_snapshot(cron.repo_root, "atm-phase-bh").value
        actual_state = load_state(cron.repo_root, "atm-phase-bh").value
        assert replace(actual_snapshot, files={}) == replace(expected_snapshot, files={})
        assert actual_state == expected_state
        assert output == cli.to_json({"at": ACTION.at, "kind": ACTION.kind, "to": ACTION.to,
                                      "bead": ACTION.bead_ids[0], "text": ACTION.text})


def test_headless_held_lock_is_quiet_when_snapshot_is_fresh(monkeypatch, capsys) -> None:
    class Clock:
        @staticmethod
        def now(_timezone): return SNAPSHOT.generated_at

    monkeypatch.setattr(cli, "datetime", Clock)
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Err(LOCK_HELD))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(SNAPSHOT))
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["--once", "--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert capsys.readouterr().out == ""


def test_headless_held_lock_reports_stale_snapshot_with_injected_clock(monkeypatch, capsys) -> None:
    class Clock:
        @staticmethod
        def now(_timezone): return SNAPSHOT.generated_at.replace(year=SNAPSHOT.generated_at.year + 1)

    monkeypatch.setattr(cli, "datetime", Clock)
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Err(LOCK_HELD))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(SNAPSHOT))
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    assert cli.main(["--once", "--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert "stopped updating" in capsys.readouterr().out


def test_show_missing_snapshot_never_starts_runner(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Err(NotFound("not_found", "snapshot", "run a console tick")))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 2
    assert "is not running for phase bh" in capsys.readouterr().out


def test_snapshot_runs_a_dry_tick_without_headless_output(monkeypatch, capsys) -> None:
    seen = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **kwargs: Ok(replace(CONFIG, dry_run=kwargs["dry_run"])))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Ok(type("Lock", (), {"__enter__": lambda s: s, "__exit__": lambda *_: None})()))
    monkeypatch.setattr(cli.runner, "step", lambda config, *_: (seen.append(config) or (SNAPSHOT, cli.EMPTY)))
    assert cli.main(["snapshot", "--phase", "bh", "--as", "fenix"]) == 0
    assert seen[0].dry_run and capsys.readouterr().out == ""
