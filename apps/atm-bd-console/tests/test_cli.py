from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from contract_samples import (
    ACTION,
    BEAD,
    CONFIG,
    MERGE,
    PHASE_FILE,
    REMOTE_REF,
    REPO_CONFIG,
    SNAPSHOT,
    SPRINT,
    TIMEOUT,
    WORKTREE,
)

from atm_bd_console import __version__, app, cli, runner
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.state import acquire_lock, load_snapshot, load_state
from atm_bd_console.types import INITIAL_STATE

PKG = Path(__file__).resolve().parents[1]


def test_missing_as_uses_documented_message(capsys) -> None:
    assert cli.main(["--phase", "bh"]) == 3
    message = capsys.readouterr().out.strip()
    assert "--as <phase lead> is required" in message
    assert "the ATM identity that leads this phase" in message
    assert "Tasks are assigned as that identity" in message
    assert "escalations go to it when no escalation recipient is set" in message
    assert "The phase lead varies by phase" in message
    assert "never inferred from $ATM_IDENTITY or the repository's lead" in message


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
    (repo / ".atm-bd/console.toml").write_text('[console]\ngithub = "off"\n', encoding="utf-8")
    (repo / "docs/plans/phase-bh.jsonl").write_text('{"sprint": "bh-1"}\n', encoding="utf-8")
    beads = [
        {"id": "atm-phase-bh", "title": "Smoke phase", "status": "open", "labels": ["phase-bh"]},
        {"id": "atm-bh-1", "title": "Smoke sprint", "status": "open", "parent": "atm-phase-bh",
         "metadata": {"layer": 1, "branch": "sprint/bh-1", "pr_target": "main", "stack": "phase-bh"}},
    ]
    (repo / "beads.json").write_text(json.dumps(beads), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    scripts = {
        "bd": 'case "$1" in show|list) cat beads.json;; ready) printf "[]\\n";; *) exit 97;; esac\n',
        "atm": 'case "$1:$2" in members:--team) printf \'{"members": []}\\n\';; task:list) printf "[]\\n";; *) exit 97;; esac\n',
        "git": 'case "$1" in fetch|for-each-ref) exit 0;; worktree) printf "worktree %s\\nHEAD %s\\nbranch refs/heads/main\\n" "$PWD" "0123456789abcdef";; *) exit 97;; esac\n',
    }
    for name, script in scripts.items():
        executable = fake_bin / name
        executable.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        executable.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(fake_bin), "/usr/bin", "/bin"))
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    installed = environment.get("ATM_BD_CONSOLE_BIN")
    if installed:
        command = [str(Path(installed).resolve(strict=True))]
    else:
        uv = shutil.which("uv")
        assert uv is not None, "uv is required to run the console entry point"
        command = [uv, "run", "--frozen", "--project", str(PKG), "atm-bd-console"]

    help_result = subprocess.run(
        [*command, "--help"],
        capture_output=True, text=True, check=False, cwd=repo, env=environment,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "run one tick and exit" in help_result.stdout

    check = subprocess.run(
        [*command, "--headless", "--phase", "bh", "--as", "fenix", "--dry-run"],
        capture_output=True, text=True, check=False, cwd=repo, env=environment,
    )
    assert check.returncode == 0, check.stderr + check.stdout
    assert check.stdout == ""
    saved = json.loads((repo / ".atm-bd/atm-bd-console/atm-phase-bh/snapshot.json").read_text())
    assert saved["phase"] == "bh" and saved["stopped"] is None
    assert saved["errors"] == [] and len(saved["sprints"]) == 1
    assert saved["sprints"][0]["id"] == "atm-bh-1"


def test_ci_covers_minimum_latest_and_installed_cron_smoke() -> None:
    workflow = (PKG.parents[1] / ".github/workflows/tests.yml").read_text()
    console = workflow.split("  console-tests:\n", 1)[1].split("\n  package-tests:", 1)[0]
    assert 'python-version: ["3.11", "3.x"]' in console
    assert "uv tool install --python '${{ steps.python.outputs.python-path }}' ./apps/atm-bd-console" in console
    assert "pytest -q\n" in console
    assert '"$ATM_BD_CONSOLE_BIN" --help' in console
    assert "ATM_BD_CONSOLE_BIN: ${{ env.UV_TOOL_BIN_DIR }}/atm-bd-console" in console
    assert "tests/test_cli.py::test_installed_cron_command_runs_one_headless_tick" in console


def test_ci_job_tool_paths_use_a_supported_context() -> None:
    workflow = (PKG.parents[1] / ".github/workflows/tests.yml").read_text()
    console = workflow.split("  console-tests:\n", 1)[1].split("\n  package-tests:", 1)[0]
    job_environment = console.split("    env:\n", 1)[1].split("    strategy:\n", 1)[0]
    assert "${{ runner." not in job_environment
    assert "UV_TOOL_DIR: ${{ github.workspace }}/../console-tools" in job_environment
    assert "UV_TOOL_BIN_DIR: ${{ github.workspace }}/../console-bin" in job_environment


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
        def load_state(self): return Ok(INITIAL_STATE)

    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Ok(Lock()))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped="idle", errors=()), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped="closed", errors=()), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped=None, errors=(TIMEOUT,)), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped="closed", errors=(TIMEOUT,)), None))
    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out


def test_headless_action_json_has_delivery_outcome(monkeypatch, capsys) -> None:
    class Lock:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def load_state(self): return Ok(INITIAL_STATE)

    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Ok(Lock()))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (replace(SNAPSHOT, stopped=None, errors=(), sent=(replace(ACTION, outcome="failed command_failed"),)), INITIAL_STATE))

    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed command_failed"


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


def test_interactive_launch_defers_state_loading_to_the_locked_app(monkeypatch) -> None:
    seen: list[tuple] = []
    kwargs = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(app, "run", lambda *args, **values: seen.append(args) or kwargs.append(values) or 0)

    assert cli.main(["--phase", "bh", "--as", "fenix"]) == 0
    assert seen == [(CONFIG, None, INITIAL_STATE, None)]
    assert kwargs == [{"runner_step": cli.runner.step}]


def test_app_bound_runner_step_uses_the_lock_store(monkeypatch, tmp_path) -> None:
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / "atm-phase-bh"))
    seen: list[object] = []

    class FakeApp:
        def __init__(self, _config, step, state, _startup_error):
            self.step = step
            self.state = state

        def run(self):
            self.step(config, self.state, False)
            return 0

    def runner_step(current, current_state, force, store):
        seen.extend((current, current_state, force, store))
        return SNAPSHOT, current_state

    monkeypatch.setattr(app, "ConsoleApp", FakeApp)
    assert app.run(config, None, INITIAL_STATE, runner_step=runner_step) == 0
    assert seen[:3] == [config, INITIAL_STATE, False]
    assert seen[3] is not None


def test_corrupt_state_is_reported_and_persisted_for_a_closed_phase(monkeypatch, tmp_path, capsys):
    config = replace(CONFIG, repo_root=str(tmp_path))
    phase_file = replace(PHASE_FILE, sprints=(), depends_on={})
    root = replace(BEAD, id=PHASE_FILE.root_id, status="closed")
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(config))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(phase_file))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Err(TIMEOUT))
    directory = tmp_path / ".atm-bd/atm-bd-console" / PHASE_FILE.root_id
    directory.mkdir(parents=True)
    (directory / "state.json").write_text("{}")

    assert cli.main(["--headless", "--phase", "bh", "--as", "fenix"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["kind"] == "error" and "state.json.corrupt-" in output["text"]
    persisted = load_snapshot(tmp_path, PHASE_FILE.root_id)
    assert isinstance(persisted, Ok) and persisted.value.stopped == "closed"
    assert len(persisted.value.errors) == 1 and persisted.value.errors[0].kind == "parse_failed"
    assert len(list(directory.glob("state.json.corrupt-*"))) == 1


@pytest.mark.parametrize("member_read_fails", (False, True))
@pytest.mark.parametrize("runtime_damage", (None, "corrupt", "unreadable"))
def test_headless_cli_and_app_run_real_runner_step_with_state_store(
    monkeypatch, tmp_path, capsys, member_read_fails: bool, runtime_damage,
) -> None:
    """Both entry paths share runner.step, persistence, an action, and source-error reporting."""
    fixed_now = datetime(2026, 10, 15, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    phase_file = replace(PHASE_FILE, sprints=(SPRINT.id,), depends_on={SPRINT.id: ()})
    root = replace(BEAD, id=phase_file.root_id, title="phase", status="open", labels=(phase_file.label,), parent=None, metadata={})
    sprint = replace(
        BEAD, id=SPRINT.id, title=SPRINT.title, status="open", labels=("stage:sprint",), parent=root.id,
        metadata={"layer": SPRINT.layer, "branch": SPRINT.branch, "pr_target": SPRINT.target, "stack": SPRINT.stack},
    )
    sanity = replace(
        BEAD, id="atm-bh-6.group-sanity", title="sanity", status="open", labels=("stage:dev-sanity",),
        parent=sprint.id, assignee=None,
        metadata={
            "dev_bead": "atm-bh-6.group-dev", "sprint": "bh-6", "sprint_bead": sprint.id,
            "branch": SPRINT.branch, "pr_target": SPRINT.target, "worktree": WORKTREE.path,
            "layer": SPRINT.layer, "difficulty": "normal",
        },
    )
    beads = {root.id: root, sprint.id: sprint, sanity.id: sanity}
    merge = replace(MERGE, branch=SPRINT.branch, target=SPRINT.target, state="clean", files=(), messages=())
    repo = tmp_path / "repo"
    repo.mkdir()
    config = replace(
        CONFIG, github="off", dry_run=False, no_fetch=False, repo_root=str(repo),
        state_dir=str(repo / ".atm-bd/atm-bd-console/atm-phase-bh"),
    )
    actions = []

    class Logger:
        def event(self, *_args, **_kwargs):
            return Ok(None)

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(phase_file))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda ids: Ok(tuple(beads[item] for item in ids if item in beads)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok((sanity,)))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok((sanity,)))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Err(TIMEOUT) if member_read_fails else Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "assign", lambda *_: actions.append("assign") or Ok(None))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok((WORKTREE,)))
    monkeypatch.setattr(runner.git_source, "fetch", lambda *_: Ok(None))
    monkeypatch.setattr(runner.git_source, "remote_refs", lambda *_: Ok((REMOTE_REF,)))
    monkeypatch.setattr(runner.git_source, "merge", lambda *_: Ok(merge))
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: repo)
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(config))
    monkeypatch.setattr(cli, "now", lambda: fixed_now)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin/tool")

    damaged = {"state.json": b'{"problems":[]}', "merge.json": b'{', "prs.json": b'\xff'}

    def damage_runtime():
        if runtime_damage is None:
            return
        directory = Path(config.state_dir)
        directory.mkdir(parents=True)
        for name, payload in damaged.items():
            path = directory / name
            path.write_bytes(payload)
            if runtime_damage == "unreadable":
                path.chmod(0)

    def assert_preserved(snapshot):
        if runtime_damage is None:
            return
        for name, payload in damaged.items():
            backups = list(Path(config.state_dir).glob(f"{name}.corrupt-*"))
            assert len(backups) == 1
            backups[0].chmod(0o600)
            assert backups[0].read_bytes() == payload
            assert any(str(backups[0]) in error.fix for error in snapshot.errors)
            assert (Path(config.state_dir) / name).read_bytes() != payload

    damage_runtime()
    assert cli.main(["--phase", "bh", "--as", "fenix", "--headless"]) == 0
    headless_output = capsys.readouterr().out
    headless_snapshot = load_snapshot(repo, phase_file.root_id)
    headless_state = load_state(repo, phase_file.root_id)
    assert isinstance(headless_snapshot, Ok) and isinstance(headless_state, Ok)
    assert any(json.loads(line)["kind"] == "assign" for line in headless_output.splitlines())
    assert_preserved(headless_snapshot.value)
    if runtime_damage is not None:
        for name in damaged:
            assert any(name in json.loads(line)["text"] for line in headless_output.splitlines() if json.loads(line)["kind"] == "error")

    shutil.rmtree(config.state_dir)
    damage_runtime()
    app_result = []

    def run_in_pilot(console_app):
        async def scenario():
            async with console_app.run_test() as pilot:
                await asyncio.wait_for(pilot.app.workers.wait_for_complete(), timeout=5)
                await pilot.pause()
                if runtime_damage is not None:
                    assert console_app.is_running
                    assert "Errors" in str(console_app.query_one("#dashboard").render())
            app_result.append((console_app.snapshot, console_app.state))

        asyncio.run(scenario())
        return 0

    monkeypatch.setattr(app.ConsoleApp, "run", run_in_pilot)
    assert cli.main(["--phase", "bh", "--as", "fenix"]) == 0
    assert len(app_result) == 1 and app_result[0][0] is not None
    app_snapshot, app_state = app_result[0]
    assert actions == ["assign", "assign"]
    assert headless_snapshot.value.sent == app_snapshot.sent
    assert headless_state.value == app_state
    assert [error.kind for error in headless_snapshot.value.errors] == [error.kind for error in app_snapshot.errors]
    assert len(app_snapshot.errors) == (3 if runtime_damage else 0) + int(member_read_fails)
    assert load_snapshot(repo, phase_file.root_id) == Ok(app_snapshot)
    assert_preserved(app_snapshot)
