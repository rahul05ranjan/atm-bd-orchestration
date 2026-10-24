from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from atm_bd_console.command import Output
from atm_bd_console.config import load_config, repo_root
from atm_bd_console.result import CommandFailed, ConfigInvalid, Err, IoFailed, Ok, Timeout


def _team_config(root) -> None:
    shutil.copytree(Path(__file__).parent / "fixtures/beads/repo", root, dirs_exist_ok=True)
    (root / ".atm.toml").write_text('[atm]\ndefault_team = "atm-dev"\n')
    subprocess.run(("git", "init", "-q", str(root)), check=True)
    subprocess.run(("git", "-C", str(root), "remote", "add", "origin", "https://github.com/example/repo.git"), check=True)


def test_repo_root_resolves_the_current_directory(monkeypatch, tmp_path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert repo_root() == nested.resolve()


def test_defaults_file_and_flags_have_documented_precedence(tmp_path) -> None:
    _team_config(tmp_path)
    default = load_config(tmp_path, "bh")
    assert isinstance(default, Ok)
    assert default == Ok(default.value.__class__("bh", "fenix", "atm-dev", "graphql", 60, False, False,
                                                  str(tmp_path), str(tmp_path / ".atm-bd/atm-bd/atm-phase-bh"),
                                                  code_root=str(tmp_path), github_repo="example/repo"))
    assert default.value.notification_sender == "atm-bd@tool"
    path = tmp_path / ".atm-bd"
    path.mkdir(exist_ok=True)
    (path / "console.toml").write_text("[console]\ngithub = 'rest'\ninterval = 90\n")
    loaded = load_config(tmp_path, "bh", github="off", interval_s=120, dry_run=True, no_fetch=True)
    assert isinstance(loaded, Ok)
    assert loaded == Ok(loaded.value.__class__("bh", "fenix", "atm-dev", "off", 120, True, True,
                                               str(tmp_path), str(tmp_path / ".atm-bd/atm-bd/atm-phase-bh"),
                                               code_root=str(tmp_path), github_repo="example/repo"))


def test_notification_sender_only_uses_repo_yaml_not_environment_or_console_toml(tmp_path, monkeypatch) -> None:
    _team_config(tmp_path)
    path = tmp_path / ".atm-bd" / "console.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("[console]\nnotification_sender = 'wrong@source'\n")
    monkeypatch.setenv("ATM_IDENTITY", "wrong")
    monkeypatch.setenv("ATM_TEAM", "source")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Ok) and result.value.notification_sender == "atm-bd@tool"
    path = tmp_path / ".claude/project/atm-bd-orchestration.yaml"
    path.write_text('tool_identity: "notify@automation" # repo override\n')
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Ok) and result.value.notification_sender == "notify@automation"

    for invalid in ("notify", "@tool", "notify@", "notify@tool@other", "bad name@tool", "42"):
        path.write_text(f"tool_identity: '{invalid}'\n")
        result = load_config(tmp_path, "bh")
        assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
        assert result.error.key == "tool_identity"


def test_invalid_repository_yaml_is_a_typed_configuration_error(tmp_path) -> None:
    _team_config(tmp_path)
    path = tmp_path / ".claude/project/atm-bd-orchestration.yaml"
    path.write_text("tool_identity: [\n")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == str(path)


def test_github_budget_configuration_and_validation(tmp_path) -> None:
    _team_config(tmp_path)
    path = tmp_path / ".atm-bd" / "console.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("[console]\ngithub_poll_interval = 120\ngithub_quota_threshold = 70\n")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Ok)
    assert (result.value.github_poll_interval_s, result.value.github_quota_threshold) == (120, 70)
    overridden = load_config(tmp_path, "bh", github_poll_interval_s=30, github_quota_threshold=20)
    assert isinstance(overridden, Ok)
    assert (overridden.value.github_poll_interval_s, overridden.value.github_quota_threshold) == (30, 20)
    for value in (0, True):
        assert isinstance(load_config(tmp_path, "bh", github_poll_interval_s=value), Err)
    for value in (-1, 101):
        assert isinstance(load_config(tmp_path, "bh", github_quota_threshold=value), Err)


def test_code_checkout_override_uses_phase_path_and_explicit_github_identity(tmp_path, monkeypatch) -> None:
    beads_root, code_root = tmp_path / "atm-core", tmp_path / "atm-bd-orchestration"
    beads_root.mkdir()
    code_root.mkdir()
    _team_config(beads_root)
    phase = beads_root / ".atm-bd" / "phase-bh.toml"
    phase.write_text(phase.read_text().replace("[templates]", 'code_checkout = "../atm-bd-orchestration"\n\n[templates]'))
    calls = []

    def fake_run(argv, _fix, *, cwd, **_kwargs):
        calls.append((tuple(argv), Path(cwd)))
        if argv == ("git", "rev-parse", "--show-toplevel"):
            return Ok(Output(0, str(code_root.resolve())))
        if argv == ("git", "remote", "get-url", "origin"):
            return Ok(Output(0, "git@github.com:sample/code.git\n"))
        raise AssertionError(argv)

    monkeypatch.setattr("atm_bd_console.config.command.run", fake_run)
    loaded = load_config(beads_root, "bh")
    assert isinstance(loaded, Ok)
    assert loaded.value.repo_root == str(beads_root)
    assert loaded.value.state_dir == str(beads_root / ".atm-bd/atm-bd/atm-phase-bh")
    assert loaded.value.code_root == str(code_root.resolve())
    assert loaded.value.github_repo == "sample/code"
    assert calls == [
        (("git", "rev-parse", "--show-toplevel"), code_root.resolve()),
        (("git", "remote", "get-url", "origin"), code_root.resolve()),
    ]


def test_failed_toplevel_read_keeps_the_original_command_error(tmp_path, monkeypatch) -> None:
    _team_config(tmp_path)
    phase = tmp_path / ".atm-bd" / "phase-bh.toml"
    phase.write_text(phase.read_text().replace("[templates]", 'code_checkout = "../not-a-repo"\n\n[templates]'))

    def fake_run(argv, _fix, **_kwargs):
        assert argv == ("git", "rev-parse", "--show-toplevel")
        return Err(CommandFailed("command_failed", argv, 128, "not a git repository", "fix it"))

    monkeypatch.setattr("atm_bd_console.config.command.run", fake_run)
    result = load_config(tmp_path, "bh")
    # The original command error reaches the caller unchanged (argv, code, stderr), not a re-invented ConfigInvalid.
    assert result == Err(CommandFailed("command_failed", ("git", "rev-parse", "--show-toplevel"), 128,
                                       "not a git repository", "fix it"))


def test_timed_out_toplevel_read_keeps_its_timeout_error(tmp_path, monkeypatch) -> None:
    _team_config(tmp_path)
    timeout = Timeout("timeout", ("git", "rev-parse", "--show-toplevel"), 20, "retry")
    monkeypatch.setattr("atm_bd_console.config.command.run", lambda *_a, **_k: Err(timeout))
    assert load_config(tmp_path, "bh") == Err(timeout)


def test_non_github_origin_is_a_named_configuration_error(tmp_path, monkeypatch) -> None:
    _team_config(tmp_path)
    calls = []

    def fake_run(argv, _fix, *, cwd, **_kwargs):
        calls.append(argv)
        if argv == ("git", "rev-parse", "--show-toplevel"):
            return Ok(Output(0, str(Path(cwd).resolve())))
        return Ok(Output(0, "https://gitlab.example/group/project.git\n"))

    monkeypatch.setattr("atm_bd_console.config.command.run", fake_run)
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == "code_checkout.origin"
    assert "GitHub repository URL" in result.error.fix
    assert calls == [("git", "rev-parse", "--show-toplevel"), ("git", "remote", "get-url", "origin")]


def test_missing_origin_keeps_the_original_command_error(tmp_path, monkeypatch) -> None:
    _team_config(tmp_path)

    def fake_run(argv, _fix, *, cwd, **_kwargs):
        if argv == ("git", "rev-parse", "--show-toplevel"):
            return Ok(Output(0, str(Path(cwd).resolve())))
        return Err(missing)

    missing = CommandFailed("command_failed", ("git", "remote", "get-url", "origin"), 2, "no such remote: origin", "set origin")
    monkeypatch.setattr("atm_bd_console.config.command.run", fake_run)
    result = load_config(tmp_path, "bh")
    # The original error (argv, code, stderr) is kept, not re-invented as ConfigInvalid.
    assert result == Err(missing)


def test_invalid_github_and_short_interval_name_key_value_and_fix(tmp_path) -> None:
    _team_config(tmp_path)
    for text, key, value, allowed in (
        ("[console]\ngithub = 'broken'\n", "github", "'broken'", "graphql, rest, or off"),
        ("[console]\ninterval = 5\n", "interval", "5", "at least 10 seconds"),
    ):
        (tmp_path / ".atm-bd").mkdir(exist_ok=True)
        (tmp_path / ".atm-bd" / "console.toml").write_text(text)
        result = load_config(tmp_path, "bh")
        assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
        assert result.error.key == key and result.error.value == value and allowed in result.error.fix


def test_malformed_console_toml_is_config_invalid_naming_the_file(tmp_path) -> None:
    _team_config(tmp_path)
    (tmp_path / ".atm-bd").mkdir(exist_ok=True)
    path = tmp_path / ".atm-bd" / "console.toml"
    path.write_text("[console\ngithub = ")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == str(path) and result.error.value
    assert "graphql, rest, or off" in result.error.fix and "at least 10" in result.error.fix


def test_undecodable_console_toml_is_a_typed_io_error(tmp_path) -> None:
    _team_config(tmp_path)
    (tmp_path / ".atm-bd").mkdir(exist_ok=True)
    (tmp_path / ".atm-bd" / "console.toml").write_bytes(b"\xff\xfe not utf-8")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert result.error.path == str(tmp_path / ".atm-bd" / "console.toml")


def test_missing_atm_toml_is_a_typed_io_error_naming_the_file(tmp_path) -> None:
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert result.error.path == str(tmp_path / ".atm.toml")
    assert "restore the repository .atm.toml" in result.error.fix


def test_malformed_atm_toml_is_config_invalid_naming_the_file(tmp_path) -> None:
    (tmp_path / ".atm.toml").write_text("[atm\ndefault_team = ")
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == str(tmp_path / ".atm.toml") and result.error.value
    assert "correct the repository .atm.toml syntax" in result.error.fix


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("[other]\nx = 1\n", "None"),
        ('[atm]\ndefault_team = ""\n', "''"),
        ("[atm]\ndefault_team = 7\n", "7"),
    ],
    ids=["absent", "empty", "non-string"],
)
def test_absent_empty_or_non_string_default_team_is_config_invalid(tmp_path, text, value) -> None:
    (tmp_path / ".atm.toml").write_text(text)
    result = load_config(tmp_path, "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == "atm.default_team" and result.error.value == value
    assert "set atm.default_team in .atm.toml to a nonempty team name" in result.error.fix
