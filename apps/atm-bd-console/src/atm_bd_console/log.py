"""One file-only sc-observability logger for each console process."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from sc_observability import Err as ObservabilityErr
from sc_observability import LogEvent, Logger, LoggerConfig, create_logger

from .atm_bd import PHASE_DIR
from .result import Err, IoFailed, Ok, Result

EventAction = Literal["tick_start", "tick_end", "read", "github_read", "action", "hold", "error"]


class ConsoleLogger:
    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    @property
    def native(self) -> Logger:
        return self._logger

    def event(self, action: EventAction, **fields: object) -> Result[None]:
        admitted = self._logger.log(LogEvent("info", "atm_bd_console", action, fields=fields))
        if isinstance(admitted, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(admitted.error), "inspect the console log directory"))
        return Ok(None)


def create_console_logger(repo_root: str | Path) -> Result[ConsoleLogger]:
    log_root = Path(repo_root) / PHASE_DIR / "atm-bd-console" / "logs"
    try:
        log_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return Err(IoFailed("io_failed", str(log_root), str(error), "repair the log directory permissions"))
    created = create_logger(LoggerConfig(service="atm-bd-console", log_root=str(log_root), enable_console_sink=False))
    if isinstance(created, ObservabilityErr):
        return Err(IoFailed("io_failed", str(log_root), str(created.error), "restore sc-observability"))
    return Ok(ConsoleLogger(created.value))
