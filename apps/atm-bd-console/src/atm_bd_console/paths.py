"""The one owner of every console runtime path under a repository's .atm-bd directory."""
from __future__ import annotations

from pathlib import Path

from .atm_bd import PHASE_DIR

# The console's directory name under .atm-bd, and the service name its log file is named after.
CONSOLE_NAME = "atm-bd"


def console_config_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / PHASE_DIR / "console.toml"


def console_root(repo_root: str | Path) -> Path:
    return Path(repo_root) / PHASE_DIR / CONSOLE_NAME


def log_directory(repo_root: str | Path) -> Path:
    return console_root(repo_root) / "logs"


def state_directory(repo_root: str | Path, root_id: str) -> Path:
    return console_root(repo_root) / root_id
