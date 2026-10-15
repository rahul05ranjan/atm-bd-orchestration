from __future__ import annotations

from atm_bd_console.config import load_config
from atm_bd_console.result import ConfigInvalid, Err, IoFailed, Ok


def test_defaults_file_and_flags_have_documented_precedence(tmp_path) -> None:
    default = load_config(tmp_path, "atm-bh", "fenix")
    assert default == Ok(default.value.__class__("atm-bh", "fenix", "graphql", 60, False, False, str(tmp_path), str(tmp_path / ".atm-bd/atm-bd-console/atm-bh")))
    path = tmp_path / ".atm-bd"
    path.mkdir()
    (path / "console.toml").write_text("[console]\ngithub = 'rest'\ninterval = 90\n")
    loaded = load_config(tmp_path, "atm-bh", "fenix", github="off", interval_s=120, dry_run=True, no_fetch=True)
    assert loaded == Ok(loaded.value.__class__("atm-bh", "fenix", "off", 120, True, True, str(tmp_path), str(tmp_path / ".atm-bd/atm-bd-console/atm-bh")))


def test_invalid_github_and_short_interval_name_key_value_and_fix(tmp_path) -> None:
    for text, key, value in (("[console]\ngithub = 'broken'\n", "github", "'broken'"), ("[console]\ninterval = 5\n", "interval", "5")):
        (tmp_path / ".atm-bd").mkdir(exist_ok=True)
        (tmp_path / ".atm-bd" / "console.toml").write_text(text)
        result = load_config(tmp_path, "atm-bh", "fenix")
        assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
        assert result.error.key == key and result.error.value == value and result.error.fix


def test_undecodable_console_toml_is_a_typed_io_error(tmp_path) -> None:
    (tmp_path / ".atm-bd").mkdir()
    (tmp_path / ".atm-bd" / "console.toml").write_bytes(b"\xff\xfe not utf-8")
    result = load_config(tmp_path, "atm-bh", "fenix")
    assert isinstance(result, Err) and isinstance(result.error, IoFailed)
    assert result.error.path == str(tmp_path / ".atm-bd" / "console.toml")
