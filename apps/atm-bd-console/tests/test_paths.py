from __future__ import annotations

from atm_bd_console.config import load_config
from atm_bd_console.log import create_console_logger
from atm_bd_console.paths import console_config_path, log_directory, state_directory
from atm_bd_console.result import Ok


def test_every_console_path_resolves_under_one_root(tmp_path) -> None:
    assert console_config_path(tmp_path) == tmp_path / ".atm-bd" / "console.toml"
    assert log_directory(tmp_path) == tmp_path / ".atm-bd" / "atm-bd" / "logs"
    assert state_directory(tmp_path, "atm-bh") == tmp_path / ".atm-bd" / "atm-bd" / "atm-bh"


def test_config_state_dir_is_the_state_directory(phase_repo) -> None:
    tmp_path = phase_repo
    (tmp_path / ".atm.toml").write_text('[atm]\ndefault_team = "atm-dev"\n')
    loaded = load_config(tmp_path, "bh")
    assert isinstance(loaded, Ok)
    assert loaded.value.state_dir == str(state_directory(tmp_path, "atm-phase-bh"))


def test_logger_writes_into_the_log_directory(tmp_path) -> None:
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)
    assert created.value.event("tick_start", source="test", ok=True) == Ok(None)
    assert created.value.flush() == Ok(None)
    assert [path.name for path in log_directory(tmp_path).glob("*.jsonl")] == ["atm-bd.log.jsonl"]
