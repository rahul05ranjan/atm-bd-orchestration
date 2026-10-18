"""Load the console's small, explicit runtime configuration."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from .atm_bd import REPO_CONFIG_PATH, phase_file
from .paths import console_config_path, state_directory
from .result import ConfigInvalid, Err, IoFailed, Ok, Result
from .types import DEFAULT_NOTIFICATION_SENDER, Config

_GITHUB = frozenset(("graphql", "rest", "off"))


def repo_root() -> Path:
    """Resolve the repository root used by every command-line mode."""
    return Path.cwd().resolve()


def _team(root: Path) -> Result[str]:
    path = root / ".atm.toml"
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        return Err(IoFailed("io_failed", str(path), str(error), "restore the repository .atm.toml"))
    except tomllib.TOMLDecodeError as error:
        return Err(ConfigInvalid("config_invalid", str(path), str(error), "correct the repository .atm.toml syntax"))
    atm = values.get("atm") if isinstance(values, dict) else None
    team = atm.get("default_team") if isinstance(atm, dict) else None
    if not isinstance(team, str) or not team:
        return Err(ConfigInvalid("config_invalid", "atm.default_team", repr(team), "set atm.default_team in .atm.toml to a nonempty team name"))
    return Ok(team)


def load_config(
    repo_root: str | Path,
    phase: str,
    *,
    github: str | None = None,
    interval_s: int | None = None,
    dry_run: bool = False,
    no_fetch: bool = False,
) -> Result[Config]:
    """Return TOML settings with explicit command-line overrides.

    Missing console.toml uses the documented graphql/60 and notification sender defaults. The phase
    file must supply the lead and all assignment template paths.
    """
    root = Path(repo_root)
    team = _team(root)
    if isinstance(team, Err):
        return team
    config_path = console_config_path(root)
    values: object = {}
    if config_path.exists():
        try:
            values = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            return Err(IoFailed("io_failed", str(config_path), str(error), "correct or restore console.toml"))
        except tomllib.TOMLDecodeError as error:
            return Err(ConfigInvalid("config_invalid", str(config_path), str(error),
                                     "correct the TOML syntax; console.github is graphql, rest, or off and console.interval is an integer of at least 10 seconds"))
    if not isinstance(values, dict):
        return Err(ConfigInvalid("config_invalid", "console", repr(values), "use a [console] table"))
    console = values.get("console", {})
    if not isinstance(console, dict):
        return Err(ConfigInvalid("config_invalid", "console", repr(console), "use a [console] table"))

    configured_github = console.get("github", "graphql")
    configured_interval = console.get("interval", 60)
    repo_path = root / REPO_CONFIG_PATH
    try:
        repo_values = yaml.safe_load(repo_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        repo_values = {}
    except (OSError, UnicodeDecodeError) as error:
        return Err(IoFailed("io_failed", str(repo_path), str(error), "restore the repository configuration"))
    except yaml.YAMLError as error:
        return Err(ConfigInvalid("config_invalid", str(repo_path), str(error), "correct the repository YAML syntax"))
    if repo_values is None:
        repo_values = {}
    if not isinstance(repo_values, dict):
        return Err(ConfigInvalid("config_invalid", str(repo_path), repr(repo_values), "use a YAML mapping"))
    notification_sender = repo_values.get("tool_identity", DEFAULT_NOTIFICATION_SENDER)
    chosen_github = github if github is not None else configured_github
    chosen_interval = interval_s if interval_s is not None else configured_interval
    if not isinstance(chosen_github, str) or chosen_github not in _GITHUB:
        return Err(ConfigInvalid("config_invalid", "github", repr(chosen_github), "use graphql, rest, or off"))
    if not isinstance(chosen_interval, int) or isinstance(chosen_interval, bool) or chosen_interval < 10:
        return Err(ConfigInvalid("config_invalid", "interval", repr(chosen_interval), "use an integer of at least 10 seconds"))
    if not isinstance(notification_sender, str) or not re.fullmatch(r"[A-Za-z0-9_-]+@[A-Za-z0-9_-]+", notification_sender):
        return Err(ConfigInvalid("config_invalid", "tool_identity", repr(notification_sender),
                                 f"set tool_identity in {repo_path} to member@team"))

    phase_settings = phase_file(str(root), phase)
    if isinstance(phase_settings, Err):
        return phase_settings
    state_dir = state_directory(root, f"atm-phase-{phase}")
    return Ok(Config(phase, phase_settings.value.lead, team.value, chosen_github, chosen_interval, dry_run, no_fetch,
                     str(root), str(state_dir), notification_sender=notification_sender))
