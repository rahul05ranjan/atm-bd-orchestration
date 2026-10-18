from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import HANG_GUARD_S, run_scenario
from contract_samples import (
    ACTION,
    BEAD,
    CONFIG,
    IO_FAILED,
    MERGE,
    PHASE,
    PHASE_FILE,
    REMOTE_REF,
    REPO_CONFIG,
    SNAPSHOT,
    SPRINT,
    TIMEOUT,
    WORKTREE,
)
from process_helpers import run_process
from rich.text import Text

from atm_bd_console import app, cli, runner
from atm_bd_console.build import identity
from atm_bd_console.enums import MergeState, SprintState
from atm_bd_console.result import CommandFailed, Err, IoFailed, LockHeld, NotFound, Ok
from atm_bd_console.state import acquire_lock, load_snapshot, load_state
from atm_bd_console.status_table import markdown, rows
from atm_bd_console.types import INITIAL_STATE, Due

PKG = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.fixture
def cli_runtime(monkeypatch, tmp_path):
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / ".atm-bd/atm-bd" / PHASE_FILE.root_id))
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: tmp_path)

    def load_config(_root, _phase, **options):
        return Ok(replace(config, **{key: value for key, value in options.items() if value is not None}))

    monkeypatch.setattr(cli.config_source, "load_config", load_config)
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "now", lambda: FIXED_NOW)
    return config


def test_cron_markdown_uses_shared_rows_and_dry_run(cli_runtime, monkeypatch, capsys):
    snapshot = replace(SNAPSHOT, errors=(), gh=replace(SNAPSHOT.gh, ok=True, error=None, truncated=False))
    calls = []

    def step(config, state, force, store):
        calls.append((config.dry_run, force))
        return runner.StepResult(snapshot, state)

    monkeypatch.setattr(cli.runner, "step", step)
    monkeypatch.setattr(runner.atm_source, "send", lambda *args: pytest.fail("unexpected send"))
    monkeypatch.setattr(runner.atm_source, "assign", lambda *args: pytest.fail("unexpected assignment"))
    assert cli.main(["cron", "--phase", "x", "--md-table"]) == 0
    output = capsys.readouterr()
    assert output.out == markdown(rows(snapshot)) + "\n"
    assert output.err == ""
    assert calls == [(True, True)]


def test_cron_quota_status_matches_status_bar(cli_runtime, monkeypatch, capsys):
    gh = replace(SNAPSHOT.gh, mode="graphql", core_remaining=4999, core_limit=5000,
                 core_reset=FIXED_NOW + timedelta(hours=2), graphql_remaining=2310, graphql_limit=5000,
                 graphql_reset=FIXED_NOW + timedelta(hours=1), quota_at=FIXED_NOW - timedelta(seconds=601),
                 poll_interval_s=600, quota_threshold=50, paused=True, rate_limited=False)
    snapshot = replace(SNAPSHOT, generated_at=FIXED_NOW, gh=gh, stopped=None, errors=())
    monkeypatch.setattr(cli.runner, "step", lambda config, state, force, store: runner.StepResult(snapshot, state))
    assert cli.main(["cron", "--phase", "x"]) == 0
    stdout = capsys.readouterr().out
    assert "graphql 2310/5000 ⏸" in stdout
    output = [json.loads(line) for line in stdout.splitlines()]
    assert output == [{"at": "2026-10-15T00:00:00Z", "kind": "status",
                       "text": cli.render.github_quotas(gh, FIXED_NOW)}]
    assert "core 4999/5000" in output[0]["text"] and "graphql 2310/5000 ⏸" in output[0]["text"]
    assert output[0]["text"] in cli.render.status_bar(snapshot, 200).plain
    assert "age 10m" in output[0]["text"]


def test_cron_markdown_rejects_partial_snapshot(cli_runtime, monkeypatch, capsys):
    snapshot = replace(SNAPSHOT, errors=(TIMEOUT,))
    monkeypatch.setattr(cli.runner, "step", lambda config, state, force, store: runner.StepResult(snapshot, state))
    assert cli.main(["cron", "--phase", "x", "--md-table"]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "timeout" in output.err


def test_cron_markdown_rejects_truncated_github_snapshot(cli_runtime, monkeypatch, capsys):
    snapshot = replace(SNAPSHOT, errors=(), gh=replace(SNAPSHOT.gh, ok=True, error=None, truncated=True))
    monkeypatch.setattr(cli.runner, "step", lambda config, state, force, store: runner.StepResult(snapshot, state))
    assert cli.main(["cron", "--phase", "x", "--md-table"]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "partial_snapshot" in output.err and "truncated" in output.err


def test_as_is_not_a_console_option(capsys) -> None:
    with pytest.raises(SystemExit) as rejected:
        cli.main(["console", "--phase", "bh", "--as=fenix"])
    assert rejected.value.code == 2
    assert "unrecognized arguments: --as=fenix" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["cron", "--phase", "bh", "--team=other"])
    assert "unrecognized arguments: --team=other" in capsys.readouterr().err


def test_missing_phase_is_a_launch_check(capsys) -> None:
    assert cli.main(["console"]) == 3
    assert "--phase <phase> is required" in capsys.readouterr().out


@pytest.mark.parametrize("command", ("console", "cron", "show", "snapshot"))
def test_subcommand_help(command, capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main([command, "--help"])
    assert exited.value.code == 0
    assert "--phase" in capsys.readouterr().out


@pytest.mark.parametrize("command", ("console", "cron"))
def test_headless_option_is_rejected(command, capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main([command, "--headless"])
    assert exited.value.code == 2
    assert "--headless" in capsys.readouterr().err


def test_headless_and_show_do_not_load_textual(tmp_path) -> None:
    check = run_process(
        "headless imports",
        [
            sys.executable,
            "-c",
            (
                "import sys; from atm_bd_console.cli import main; "
                "main(['cron', '--phase', 'x']); "
                "main(['show', '--phase', 'x']); "
                "assert 'textual' not in sys.modules"
            ),
        ],
        check=False, cwd=tmp_path,
    )
    assert check.returncode == 0, check.stderr


def test_installed_cron_command_runs_one_headless_tick(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fixtures = PKG / "tests/fixtures/beads/repo"
    shutil.copytree(fixtures / ".atm-bd", repo / ".atm-bd")
    shutil.copytree(fixtures / "docs/plans", repo / "docs/plans")
    shutil.copytree(fixtures / ".claude/skills", repo / ".claude/skills")
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
        command = [uv, "run", "--frozen", "--project", str(PKG), "atm-bd"]

    help_result = run_process(
        "installed command help",
        [*command, "--help"],
        check=False, cwd=repo, env=environment,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "run one tick and exit" in help_result.stdout

    check = run_process(
        "installed cron command",
        [*command, "cron", "--phase", "bh", "--dry-run"],
        check=False, cwd=repo, env=environment,
    )
    assert check.returncode == 0, check.stderr + check.stdout
    assert check.stdout == ""
    saved = json.loads((repo / ".atm-bd/atm-bd/atm-phase-bh/snapshot.json").read_text())
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
    assert "ATM_BD_CONSOLE_BIN: ${{ env.UV_TOOL_BIN_DIR }}/atm-bd" in console
    assert "tests/test_cli.py::test_installed_cron_command_runs_one_headless_tick" in console


def test_ci_job_tool_paths_use_a_supported_context() -> None:
    workflow = (PKG.parents[1] / ".github/workflows/tests.yml").read_text()
    console = workflow.split("  console-tests:\n", 1)[1].split("\n  package-tests:", 1)[0]
    job_environment = console.split("    env:\n", 1)[1].split("    strategy:\n", 1)[0]
    assert "${{ runner." not in job_environment
    assert "UV_TOOL_DIR: ${{ github.workspace }}/../console-tools" in job_environment
    assert "UV_TOOL_BIN_DIR: ${{ github.workspace }}/../console-bin" in job_environment


@pytest.mark.parametrize("stopped", (None, "closed", "idle"))
def test_show_returns_failure_for_missing_phase(monkeypatch, capsys, stopped) -> None:
    value = replace(SNAPSHOT, generated_at=FIXED_NOW, stopped=stopped, phase_data=None, errors=(TIMEOUT,))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(value))
    monkeypatch.setattr(cli, "now", lambda: FIXED_NOW)
    assert cli.main(["show", "--phase", "bh"]) == 2
    output = capsys.readouterr().out
    assert f"beads unavailable: {TIMEOUT.kind}; {TIMEOUT.fix}" in output
    if stopped is None:
        assert f"beads ⛔ {TIMEOUT.kind}" in output


def test_show_reads_snapshot_only(monkeypatch, capsys) -> None:
    clock = datetime(2026, 10, 15, tzinfo=UTC)
    monkeypatch.setattr(cli, "now", lambda: clock)
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Ok(replace(SNAPSHOT, generated_at=clock)))
    monkeypatch.setattr(cli.runner, "step", lambda *_: (_ for _ in ()).throw(AssertionError("step")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_show_fresh_running_snapshot_reads_only_snapshot(cli_runtime, monkeypatch, capsys) -> None:
    snapshot = replace(SNAPSHOT, generated_at=cli.now(), root_closed=False, stopped=None)
    loaded, rendered = [], []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("show must only read its saved snapshot")

    def load_snapshot(root, root_id):
        loaded.append((root, root_id))
        return Ok(snapshot)

    def dashboard(value, width, height):
        rendered.append(("dashboard", value, width, height))
        return Text("fresh dashboard")

    def status_bar(value, width):
        rendered.append(("status", value, width))
        return Text("fresh status")

    monkeypatch.setattr(cli, "load_snapshot", load_snapshot)
    monkeypatch.setattr(cli, "acquire_lock", forbidden)
    monkeypatch.setattr(cli.config_source, "load_config", forbidden)
    monkeypatch.setattr(cli.atm_bd, "phase_file", forbidden)
    monkeypatch.setattr(cli.atm_bd, "repo_config", forbidden)
    monkeypatch.setattr(cli.runner, "step", forbidden)
    monkeypatch.setattr(cli.render, "dashboard", dashboard)
    monkeypatch.setattr(cli.render, "status_bar", status_bar)

    assert cli.main(["show", "--phase", "bh"]) == 0
    assert loaded == [(Path(cli_runtime.repo_root), PHASE_FILE.root_id)]
    assert rendered == [("dashboard", snapshot, 120, 80), ("status", snapshot, 120)]
    assert capsys.readouterr().out == "fresh dashboard\nfresh status\n"


def test_version_and_non_headless_dispatch(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0 and capsys.readouterr().out.strip() == identity()
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "_notification_startup_error", lambda _: None)
    monkeypatch.setattr("atm_bd_console.app.run", lambda config, step, state, startup_error, **_kwargs: 17)
    assert cli.main(["console", "--phase", "bh"]) == 17


@pytest.mark.parametrize(
    ("sender", "team", "response", "error_type"),
    [
        ("atm-bd", "tool", "printf \"team 'tool' was not found\" >&2; exit 3", NotFound),
        ("atm-bd", "tool", "printf '{\"members\": []}'", NotFound),
        ("atm-bd", "tool", "printf '{\"members\": [{\"name\": \"atm-bd\"}]}'", None),
        ("robot", "ops", "printf '{\"members\": []}'", NotFound),
        ("robot", "ops", "printf '{\"members\": [{\"name\": \"robot\"}]}'", None),
        ("atm-bd", "tool", "printf 'daemon unavailable' >&2; exit 7", CommandFailed),
    ],
    ids=["team-absent", "member-absent", "default-present", "custom-absent", "custom-present", "lookup-failed"],
)
def test_console_checks_sender_at_startup(cli_runtime, fake_bin, monkeypatch, sender, team, response, error_type):
    config = replace(cli_runtime, notification_sender=f"{sender}@{team}")
    monkeypatch.setenv("ATM_IDENTITY", "unrelated")
    monkeypatch.setenv("ATM_TEAM", "unrelated")
    fake_bin.make("atm", f'test "$ATM_IDENTITY" = "{sender}" && test "$ATM_TEAM" = "{team}" || exit 90\n{response}\n')
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(config))
    seen = []
    monkeypatch.setattr(app, "run", lambda _config, _step, _state, startup_error, **_kwargs: seen.append(startup_error) or 0)

    assert cli.main(["console", "--phase", "bh"]) == 0
    assert fake_bin.argv() == [("atm", "members", "--team", team, "--json")]
    error = seen[0]
    if error_type is None:
        assert error is None
    else:
        assert isinstance(error, error_type)
        assert f"{sender}@{team}" in error.fix
        assert f"atm teams add-member {team} {sender} --agent-type tool --model none" in error.fix
        if isinstance(error, CommandFailed):
            assert error.stderr == "daemon unavailable"
            assert "could not verify" in error.fix


def test_startup_error_is_in_dashboard_before_first_refresh(monkeypatch):
    monkeypatch.setattr(cli.atm_source, "members", lambda *_: Ok(()))
    error = cli._notification_startup_error(CONFIG)
    assert isinstance(error, NotFound)
    sprints = SimpleNamespace(highlighted=None, clear_options=lambda: None, add_options=lambda _options: None)
    dummy = SimpleNamespace(
        is_running=True, startup_error=error, query_one=lambda *_: sprints,
        refresh_views=lambda: None, call_after_refresh=lambda _callback: None,
    )

    app.ConsoleApp._show(dummy, replace(SNAPSHOT, errors=(), sprints=()), "", None)

    assert dummy.snapshot.errors == (error,)
    assert "atm teams add-member tool atm-bd" in cli.render.dashboard(dummy.snapshot, 80, 80).plain


@pytest.mark.parametrize("mode", ("console", "cron", "snapshot"))
def test_github_budget_cli_options_reach_configuration(monkeypatch, mode):
    seen = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **kwargs: seen.append(kwargs) or Err(TIMEOUT))
    assert cli.main([mode, "--phase", "bh", "--github-poll-interval", "120", "--github-quota-threshold", "65"]) == 3
    assert seen[0]["github_poll_interval_s"] == 120 and seen[0]["github_quota_threshold"] == 65


@pytest.mark.parametrize("failure", ("config", "repo", "bd", "atm", "git"))
def test_launch_checks_fail_before_running(monkeypatch, capsys, failure) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("failed launch checks must not acquire a lock or run a tick")

    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Err(TIMEOUT) if failure == "config" else Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Err(TIMEOUT) if failure == "phase" else Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Err(TIMEOUT) if failure == "repo" else Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda command: None if command == failure else "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", forbidden)
    monkeypatch.setattr(cli.runner, "step", forbidden)
    assert cli.main(["cron", "--phase", "bh"]) == 3
    output = capsys.readouterr().out
    if failure in ("bd", "atm", "git"):
        assert output == f"atm-bd cron: not_found: install {failure} and retry\n"
    else:
        assert "timeout" in output


def test_show_uses_the_configured_repository_root(monkeypatch, capsys, tmp_path) -> None:
    root = tmp_path / "repository"
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: root)
    monkeypatch.setattr(cli, "load_snapshot", lambda actual_root, *_: Ok(SNAPSHOT) if actual_root == root else (_ for _ in ()).throw(AssertionError("wrong root")))
    assert cli.main(["show", "--phase", "bh"]) == 0
    assert "stopped: phase closed" in capsys.readouterr().out


def test_cron_tick_records_its_schedule_and_the_viewer_judges_it_by_that(cli_runtime, monkeypatch, capsys) -> None:
    recorded = []
    load = cli.config_source.load_config

    def record(root, phase, **options):
        recorded.append(options["interval_s"])
        return load(root, phase, **options)

    def tick(config, state, _md_table, store):
        snapshot = replace(SNAPSHOT, stopped=None, errors=(), root_closed=False, generated_at=cli.now(), interval_s=config.interval_s)
        assert store.write_snapshot(snapshot) == Ok(None)
        return runner.StepResult(snapshot, state)

    monkeypatch.setattr(cli.config_source, "load_config", record)
    monkeypatch.setattr(cli.runner, "step", tick)
    assert cli.main(["cron", "--phase", "bh"]) == 0
    assert recorded == [cli.config_source.CRON_CADENCE_S]
    stored = load_snapshot(cli_runtime.repo_root, PHASE_FILE.root_id)
    assert isinstance(stored, Ok) and stored.value.interval_s == 900

    # Liveness window is twice the recorded interval: 14 minutes after a 15-minute tick is between ticks (live);
    # 31 minutes after it, the tick due at 15 minutes has been missed by more than an interval (stopped).
    monkeypatch.setattr(cli, "now", lambda: FIXED_NOW + timedelta(minutes=14))
    assert cli.main(["show", "--phase", "bh"]) == 0
    monkeypatch.setattr(cli, "now", lambda: FIXED_NOW + timedelta(minutes=31))
    assert cli.main(["show", "--phase", "bh"]) == 2
    assert "is not running" in capsys.readouterr().out


def test_cron_cadence_yields_to_an_explicit_interval(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_a, **options: seen.append(options["interval_s"]) or Err(IoFailed("io_failed", "x", "stop", "fix")))

    cli.main(["cron", "--phase", "bh", "--interval", "300"])
    cli.main(["snapshot", "--phase", "bh"])
    cli.main(["console", "--phase", "bh"])

    assert seen == [300, None, None]


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
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(replace(SNAPSHOT, stopped="idle", errors=()), INITIAL_STATE))
    assert cli.main(["cron", "--phase", "bh"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "status"
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(replace(SNAPSHOT, stopped="closed", errors=()), INITIAL_STATE))
    assert cli.main(["cron", "--phase", "bh"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "status"
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(replace(SNAPSHOT, stopped=None, errors=(TIMEOUT,)), INITIAL_STATE))
    assert cli.main(["cron", "--phase", "bh"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(replace(SNAPSHOT, stopped="closed", errors=(TIMEOUT,)), INITIAL_STATE))
    assert cli.main(["cron", "--phase", "bh"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out
    for stopped in ("idle", "closed"):
        logging_error = replace(IO_FAILED, path="logs", fix="inspect the console log directory")
        failed_snapshot = replace(SNAPSHOT, stopped=stopped, errors=(logging_error,))
        monkeypatch.setattr(cli.runner, "step", lambda *_, value=failed_snapshot: runner.StepResult(value, INITIAL_STATE))
        assert cli.main(["cron", "--phase", "bh"]) == 0
        output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [line["kind"] for line in output] == ["status", "error"]
        assert "inspect the console log directory" in output[1]["text"]


@pytest.mark.parametrize("outcome", [None, "", "sent", "dry_run", "failed command_failed"])
def test_headless_action_json_has_delivery_outcome(monkeypatch, capsys, outcome) -> None:
    class Lock:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def load_state(self): return Ok(INITIAL_STATE)

    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Ok(Lock()))
    reported_action = replace(ACTION, outcome=outcome)
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(replace(SNAPSHOT, stopped=None, errors=(), sent=(reported_action,)), INITIAL_STATE, (reported_action,)))

    assert cli.main(["cron", "--phase", "bh"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[0]["outcome"] == outcome and output[1]["kind"] == "status"


@pytest.mark.parametrize("count", (0, 3, 6))
def test_cron_reports_all_and_only_actions_from_this_tick(cli_runtime, monkeypatch, capsys, count) -> None:
    clock = datetime(2026, 10, 15, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock if tz else clock.replace(tzinfo=None)

    class Logger:
        def event(self, *_args, **_kwargs): return Ok(None)
        def flush(self): return Ok(None)

    previous = replace(ACTION, at=clock, text="previous tick")
    old_state = replace(INITIAL_STATE, sent=(previous,))
    locked = acquire_lock(cli_runtime.repo_root, PHASE_FILE.root_id)
    assert isinstance(locked, Ok)
    with locked.value as store:
        assert store.write_state(old_state) == Ok(None)

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_bd, "phase", lambda *_: Ok(replace(PHASE, closed=False, steps=())))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    monkeypatch.setattr(runner.model, "due", lambda *_: Due(False, (), False))

    def tick(inputs, state, *_args):
        actions = tuple(replace(ACTION, at=inputs.now, text=f"action-{index}") for index in range(count))
        snapshot = replace(SNAPSHOT, generated_at=inputs.now, root_closed=False, stopped=None, errors=(), sent=(), gh=inputs.gh)
        return snapshot, actions, state

    monkeypatch.setattr(runner.model, "tick", tick)
    assert cli.main(["cron", "--phase", "bh", "--dry-run", "--github", "off"]) == 0

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["text"] for line in output] == [f"action-{index}" for index in range(count)]
    assert all(line["outcome"] == "dry_run" for line in output)
    stored = load_snapshot(cli_runtime.repo_root, PHASE_FILE.root_id)
    assert isinstance(stored, Ok)
    assert [action.text for action in stored.value.sent] == (["previous tick"] + [f"action-{index}" for index in range(count)])[-5:]


@pytest.mark.parametrize("command", ("cron", "snapshot"))
@pytest.mark.parametrize("stale", (False, True))
def test_headless_held_lock_checks_snapshot_age_without_a_tick(cli_runtime, monkeypatch, capsys, stale, command) -> None:
    generated_at = FIXED_NOW - timedelta(seconds=2 * cli_runtime.interval_s + 1 if stale else 0)
    snapshot = replace(SNAPSHOT, generated_at=generated_at, root_closed=False, stopped=None, errors=())
    acquired = acquire_lock(cli_runtime.repo_root, PHASE_FILE.root_id)
    assert isinstance(acquired, Ok)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("held lock must not run a tick or read a runtime source")

    monkeypatch.setattr(cli.runner, "step", forbidden)
    for source, name in (
        (runner.beads_source, "show"), (runner.beads_source, "open_beads"), (runner.beads_source, "ready"),
        (runner.atm_source, "members"), (runner.atm_source, "tasks"),
        (runner.git_source, "worktrees"), (runner.git_source, "fetch"),
        (runner.github_source, "open_prs"),
    ):
        monkeypatch.setattr(source, name, forbidden)
    with acquired.value as store:
        assert store.write_snapshot(snapshot) == Ok(None)
        assert cli.main([command, "--phase", "bh"]) == 0

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == (2 if stale else 1)
    assert output[0]["kind"] == "error" and output[0]["text"].startswith(f"atm-bd {command}: lock_held:")
    if stale:
        last_tick = generated_at.astimezone().strftime("%H:%M")
        assert output[1] == {
            "at": "2026-10-15T00:00:00Z", "kind": "error",
            "text": f"atm-bd {command}: console stopped updating (last tick {last_tick})",
        }


def test_snapshot_subcommand_runs_one_dry_tick_without_actions(cli_runtime, monkeypatch, capsys) -> None:
    calls = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("snapshot must not send or assign actions")

    def step(config, state, force, store):
        calls.append((config.dry_run, state, force, store))
        assert config.dry_run
        return runner.StepResult(replace(SNAPSHOT, generated_at=cli.now(), root_closed=False, stopped=None, errors=(), sent=(ACTION,)), state)

    monkeypatch.setattr(cli.runner, "step", step)
    monkeypatch.setattr(runner.atm_source, "send", forbidden)
    monkeypatch.setattr(runner.atm_source, "assign", forbidden)

    assert cli.main(["snapshot", "--phase", "bh"]) == 0
    assert len(calls) == 1 and calls[0][:3] == (True, INITIAL_STATE, False)
    assert calls[0][3].directory == Path(cli_runtime.state_dir)
    assert capsys.readouterr().out == ""


def test_headless_runs_after_the_holder_is_killed(cli_runtime, monkeypatch, capsys) -> None:
    code = (
        "import sys; from atm_bd_console.state import acquire_lock; "
        "from atm_bd_console.result import Ok; "
        "held = acquire_lock(sys.argv[1], sys.argv[2]); assert isinstance(held, Ok); "
        "print('ready', flush=True); sys.stdin.read()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, cli_runtime.repo_root, PHASE_FILE.root_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    calls = []

    def step(config, state, force, store):
        locked = acquire_lock(config.repo_root, PHASE_FILE.root_id)
        assert isinstance(locked, Err) and isinstance(locked.error, LockHeld)
        calls.append((config.repo_root, state, force, store.directory))
        return runner.StepResult(replace(SNAPSHOT, root_closed=False, stopped=None, errors=(), sent=()), state)

    monkeypatch.setattr(cli.runner, "step", step)
    try:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], HANG_GUARD_S)
        assert ready, f"lock holder did not signal readiness within {HANG_GUARD_S} seconds"
        assert child.stdout.readline().strip() == "ready", "lock holder exited before signaling readiness"
        locked = acquire_lock(cli_runtime.repo_root, PHASE_FILE.root_id)
        assert isinstance(locked, Err) and isinstance(locked.error, LockHeld)
        child.kill()
        child.wait(timeout=HANG_GUARD_S)
        assert child.returncode is not None and child.returncode < 0

        assert cli.main(["cron", "--phase", "bh"]) == 0
        assert calls == [(cli_runtime.repo_root, INITIAL_STATE, False, Path(cli_runtime.state_dir))]
        assert json.loads(capsys.readouterr().out)["kind"] == "status"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=HANG_GUARD_S)


def test_headless_prints_only_actions_from_the_current_tick(cli_runtime, monkeypatch, capsys) -> None:
    current = replace(ACTION, at=cli.now(), text="current action")
    previous = replace(ACTION, at=cli.now() - timedelta(seconds=cli_runtime.interval_s), text="old action")
    snapshot = replace(SNAPSHOT, generated_at=cli.now(), root_closed=False, stopped=None, errors=(), sent=(previous, current))
    monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(snapshot, INITIAL_STATE, (current,)))

    assert cli.main(["cron", "--phase", "bh"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == 2 and output[0]["text"] == "current action" and output[1]["kind"] == "status"


@pytest.mark.parametrize("failure_stage", ("lock", "tick"))
def test_headless_io_failures_keep_path_message_and_fix(cli_runtime, monkeypatch, capsys, failure_stage) -> None:
    directory = Path(cli_runtime.state_dir)
    if failure_stage == "lock":
        (directory / "lock").mkdir(parents=True)
        failed = acquire_lock(cli_runtime.repo_root, PHASE_FILE.root_id)
    else:
        acquired = acquire_lock(cli_runtime.repo_root, PHASE_FILE.root_id)
        assert isinstance(acquired, Ok)
        with acquired.value as store:
            (directory / "snapshot.json").mkdir()
            failed = store.write_snapshot(SNAPSHOT)
        assert isinstance(failed, Err)
        snapshot = replace(SNAPSHOT, root_closed=False, stopped=None, errors=(failed.error,), sent=())
        monkeypatch.setattr(cli.runner, "step", lambda *_: runner.StepResult(snapshot, INITIAL_STATE))
    assert isinstance(failed, Err) and isinstance(failed.error, IoFailed)

    assert cli.main(["cron", "--phase", "bh"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    errors = [line for line in output if line["kind"] == "error"]
    assert errors == [{
        "at": "2026-10-15T00:00:00Z", "kind": "error",
        "text": f"atm-bd cron: io_failed: {failed.error.path}: {failed.error.message}: {failed.error.fix}",
    }]


@pytest.mark.parametrize("command", ("cron", "snapshot"))
@pytest.mark.parametrize("error", (TIMEOUT, IO_FAILED))
def test_lock_failure_reports_full_invocation_and_error(cli_runtime, monkeypatch, capsys, command, error) -> None:
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Err(error))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Err(NotFound("not_found", "snapshot", "run")))
    monkeypatch.setattr(cli.runner, "step", lambda *_: pytest.fail("lock failure must not run a tick"))

    assert cli.main([command, "--phase", "bh"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "at": "2026-10-15T00:00:00Z", "kind": "error",
        "text": f"atm-bd {command}: {cli._error_text(error)}",
    }


def test_headless_lock_failure_is_a_json_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "acquire_lock", lambda *_: Err(TIMEOUT))
    monkeypatch.setattr(cli, "load_snapshot", lambda *_: Err(NotFound("not_found", "snapshot", "run")))
    assert cli.main(["cron", "--phase", "bh"]) == 0
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
        assert cli.main(["cron", "--phase", "bh"]) == 0
    assert '"kind": "error"' in capsys.readouterr().out


def test_interactive_launch_defers_state_loading_to_the_locked_app(monkeypatch) -> None:
    seen: list[tuple] = []
    kwargs = []
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(CONFIG))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(cli, "_notification_startup_error", lambda _: None)
    monkeypatch.setattr(app, "run", lambda *args, **values: seen.append(args) or kwargs.append(values) or 0)

    assert cli.main(["console", "--phase", "bh"]) == 0
    assert seen == [(CONFIG, None, INITIAL_STATE, None)]
    assert kwargs == [{"runner_step": cli.runner.step}]


def test_app_bound_runner_step_uses_the_lock_store(monkeypatch, tmp_path) -> None:
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / "atm-phase-bh"))
    seen: list[object] = []
    acquired = acquire_lock(tmp_path, "atm-phase-bh")
    assert isinstance(acquired, Ok)

    class FakeApp:
        def __init__(self, _config, step, state, _startup_error):
            self.step = step
            self.state = state

        def run(self):
            self.step(config, self.state, False)
            return 0

    def runner_step(current, current_state, force, store):
        assert store is acquired.value and store._lock_file is not None
        seen.extend((current, current_state, force, store))
        return SNAPSHOT, current_state

    monkeypatch.setattr(app, "ConsoleApp", FakeApp)
    monkeypatch.setattr(app, "acquire_lock", lambda *_: acquired)
    assert app.run(config, None, INITIAL_STATE, runner_step=runner_step) == 0
    assert seen[:3] == [config, INITIAL_STATE, False]
    assert seen[3] is acquired.value
    assert acquired.value._lock_file is None


def test_corrupt_state_is_reported_and_persisted_for_a_closed_phase(monkeypatch, tmp_path, capsys):
    config = replace(CONFIG, repo_root=str(tmp_path))
    phase_file = replace(PHASE_FILE, sprints=(), depends_on={})
    root = replace(BEAD, id=PHASE_FILE.root_id, status=SprintState.CLOSED)
    monkeypatch.setattr(cli.config_source, "load_config", lambda *_args, **_kwargs: Ok(config))
    monkeypatch.setattr(cli.atm_bd, "phase_file", lambda *_: Ok(phase_file))
    monkeypatch.setattr(cli.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fixture/bin")
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((root,)))
    logging_error = replace(IO_FAILED, path="logs", fix="inspect the console log directory")
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Err(logging_error))
    directory = tmp_path / ".atm-bd/atm-bd" / PHASE_FILE.root_id
    directory.mkdir(parents=True)
    (directory / "state.json").write_text("{}")

    assert cli.main(["cron", "--phase", "bh"]) == 0

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    errors = [line for line in output if line["kind"] == "error"]
    assert len(errors) == 2
    assert "state.json.corrupt-" in errors[0]["text"]
    assert logging_error.fix in errors[1]["text"]
    persisted = load_snapshot(tmp_path, PHASE_FILE.root_id)
    assert isinstance(persisted, Ok) and persisted.value.stopped == "closed"
    assert len(persisted.value.errors) == 2 and persisted.value.errors[0].kind == "parse_failed"
    assert persisted.value.errors[1] == logging_error
    assert len(list(directory.glob("state.json.corrupt-*"))) == 1


@pytest.mark.parametrize("member_read_fails", (False, True))
@pytest.mark.parametrize("runtime_damage", (
    None, "corrupt",
    pytest.param("unreadable", marks=pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="chmod permissions require a non-root POSIX user",
    )),
))
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
    root = replace(BEAD, id=phase_file.root_id, title="phase", status=SprintState.OPEN, labels=(phase_file.label,), parent=None, metadata={})
    sprint = replace(
        BEAD, id=SPRINT.id, title=SPRINT.title, status=SprintState.OPEN, labels=("stage:sprint",), parent=root.id,
        metadata={"layer": SPRINT.layer, "branch": SPRINT.branch, "pr_target": SPRINT.target, "stack": SPRINT.stack},
    )
    sanity = replace(
        BEAD, id="atm-bh-6.group-sanity", title="sanity", status=SprintState.OPEN, labels=("stage:dev-sanity",),
        parent=sprint.id, assignee=None,
        metadata={
            "dev_bead": "atm-bh-6.group-dev", "sprint": "bh-6", "sprint_bead": sprint.id,
            "branch": SPRINT.branch, "pr_target": SPRINT.target, "worktree": WORKTREE.path,
            "layer": SPRINT.layer, "difficulty": "normal",
        },
    )
    beads = {root.id: root, sprint.id: sprint, sanity.id: sanity}
    merge = replace(MERGE, branch=SPRINT.branch, target=SPRINT.target, state=MergeState.CLEAN, files=(), messages=())
    repo = tmp_path / "repo"
    repo.mkdir()
    config = replace(
        CONFIG, github="off", dry_run=False, no_fetch=False, repo_root=str(repo),
        state_dir=str(repo / ".atm-bd/atm-bd/atm-phase-bh"),
    )
    actions = []

    class Logger:
        def event(self, *_args, **_kwargs):
            return Ok(None)

        def flush(self):
            return Ok(None)

    monkeypatch.setattr(runner, "datetime", FixedDateTime)
    monkeypatch.setattr(runner, "create_console_logger", lambda *_: Ok(Logger()))
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(phase_file))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda ids: Ok(tuple(beads[item] for item in ids if item in beads)))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok((sanity,)))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok((sanity,)))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Err(TIMEOUT) if member_read_fails else Ok(()))
    monkeypatch.setattr(cli, "_notification_startup_error", lambda _: None)
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
    assert cli.main(["cron", "--phase", "bh"]) == 0
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
                await pilot.app.workers.wait_for_complete()
                await pilot.pause()
                if runtime_damage is not None:
                    assert console_app.is_running
                    assert "Errors" in str(console_app.query_one("#dashboard").render())
            app_result.append((console_app.snapshot, console_app.state))

        run_scenario(scenario())
        return 0

    monkeypatch.setattr(app.ConsoleApp, "run", run_in_pilot)
    assert cli.main(["console", "--phase", "bh"]) == 0
    assert len(app_result) == 1 and app_result[0][0] is not None
    app_snapshot, app_state = app_result[0]
    assert actions == ["assign", "assign"]
    assert headless_snapshot.value.sent == app_snapshot.sent
    assert headless_state.value == app_state
    assert [error.kind for error in headless_snapshot.value.errors] == [error.kind for error in app_snapshot.errors]
    assert len(app_snapshot.errors) == (3 if runtime_damage else 0) + int(member_read_fails)
    assert load_snapshot(repo, phase_file.root_id) == Ok(app_snapshot)
    assert_preserved(app_snapshot)
