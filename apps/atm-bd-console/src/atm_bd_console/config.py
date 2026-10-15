"""Load the console's small, explicit runtime configuration."""
from __future__ import annotations

import tomllib
from pathlib import Path

from .result import ConfigInvalid, Err, IoFailed, Ok, Result
from .types import Config

_GITHUB = frozenset(("graphql", "rest", "off"))


def load_config(
    repo_root: str | Path,
    phase: str,
    lead: str,
    *,
    github: str | None = None,
    interval_s: int | None = None,
    dry_run: bool = False,
    no_fetch: bool = False,
) -> Result[Config]:
    """Return TOML settings with explicit command-line overrides.

    A missing configuration file is deliberately unremarkable: the console is
    usable with its documented graphql/60 defaults.
    """
    root = Path(repo_root)
    config_path = root / ".atm-bd" / "console.toml"
    values: object = {}
    if config_path.exists():
        try:
            values = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            return Err(IoFailed("io_failed", str(config_path), str(error), "correct or restore console.toml"))
    if not isinstance(values, dict):
        return Err(ConfigInvalid("config_invalid", "console", repr(values), "use a [console] table"))
    console = values.get("console", {})
    if not isinstance(console, dict):
        return Err(ConfigInvalid("config_invalid", "console", repr(console), "use a [console] table"))

    configured_github = console.get("github", "graphql")
    configured_interval = console.get("interval", 60)
    chosen_github = github if github is not None else configured_github
    chosen_interval = interval_s if interval_s is not None else configured_interval
    if not isinstance(chosen_github, str) or chosen_github not in _GITHUB:
        return Err(ConfigInvalid("config_invalid", "github", repr(chosen_github), "use graphql, rest, or off"))
    if not isinstance(chosen_interval, int) or isinstance(chosen_interval, bool) or chosen_interval < 10:
        return Err(ConfigInvalid("config_invalid", "interval", repr(chosen_interval), "use an integer of at least 10 seconds"))

    state_dir = root / ".atm-bd" / "atm-bd-console" / phase
    return Ok(Config(phase, lead, chosen_github, chosen_interval, dry_run, no_fetch, str(root), str(state_dir)))
