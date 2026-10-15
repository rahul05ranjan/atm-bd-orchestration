from __future__ import annotations

import pytest

from atm_bd_console.config import load_config, repo_root
from atm_bd_console.result import ConfigInvalid, Err, IoFailed, Ok


def _team_config(root) -> None:
    (root / ".atm.toml").write_text('[atm]\ndefault_team = "atm-dev"\n')


def test_repo_root_resolves_the_current_directory(monkeypatch, tmp_path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert repo_root() == nested.resolve()


def test_defaults_file_and_flags_have_documented_precedence(tmp_path) -> None:
    _team_config(tmp_path)
    default = load_config(tmp_path, "atm-bh", "fenix")
    assert default == Ok(default.value.__class__("atm-bh", "fenix", "atm-dev", "graphql", 60, False, False, str(tmp_path), str(tmp_path / ".atm-bd/atm-bd-console/atm-phase-atm-bh")))
    path = tmp_path / ".atm-bd"
    path.mkdir()
    (path / "console.toml").write_text("[console]\ngithub = 'rest'\ninterval = 90\n")
    loaded = load_config(tmp_path, "atm-bh", "fenix", github="off", interval_s=120, dry_run=True, no_fetch=True)
    assert loaded == Ok(loaded.value.__class__("atm-bh", "fenix", "atm-dev", "off", 120, True, True, str(tmp_path), str(tmp_path / ".atm-bd/atm-bd-console/atm-phase-atm-bh")))


def test_invalid_github_and_short_interval_name_key_value_and_fix(tmp_path) -> None:
    _team_config(tmp_path)
    for text, key, value, allowed in (
        ("[console]\ngithub = 'broken'\n", "github", "'broken'", "graphql, rest, or off"),
        ("[console]\ninterval = 5\n", "interval", "5", "at least 10 seconds"),
    ):
        (tmp_path / ".atm-bd").mkdir(exist_ok=True)
        (tmp_path / ".atm-bd" / "console.toml").write_text(text)
        result = load_config(tmp_path, "atm-bh", "fenix")
        assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
        assert result.error.key == key and result.error.value == value and allowed in result.error.fix


def test_malformed_console_toml_is_config_invalid_naming_the_file(tmp_path) -> None:
    _team_config(tmp_path)
    (tmp_path / ".atm-bd").mkdir()
    path = tmp_path / ".atm-bd" / "console.toml"
    path.write_text("[console\ngithub = ")
    result = load_config(tmp_path, "atm-bh", "fenix")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == str(path) and result.error.value
    assert "graphql, rest, or off" in result.error.fix and "at least 10" in result.error.fix


def test_undecodable_console_toml_is_a_typed_io_error(tmp_path) -> None:
    _team_config(tmp_path)
    (tmp_path / ".atm-bd").mkdir()
    (tmp_path / ".atm-bd" / "console.toml").write_bytes(b"\xff\xfe not utf-8")
    result = load_config(tmp_path, "atm-bh", "fenix")
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert result.error.path == str(tmp_path / ".atm-bd" / "console.toml")


def test_missing_atm_toml_is_a_typed_io_error_naming_the_file(tmp_path) -> None:
    result = load_config(tmp_path, "atm-bh", "fenix")
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert result.error.path == str(tmp_path / ".atm.toml")
    assert "restore the repository .atm.toml" in result.error.fix


def test_malformed_atm_toml_is_config_invalid_naming_the_file(tmp_path) -> None:
    (tmp_path / ".atm.toml").write_text("[atm\ndefault_team = ")
    result = load_config(tmp_path, "atm-bh", "fenix")
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
    result = load_config(tmp_path, "atm-bh", "fenix")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == "atm.default_team" and result.error.value == value
    assert "set atm.default_team in .atm.toml to a nonempty team name" in result.error.fix
