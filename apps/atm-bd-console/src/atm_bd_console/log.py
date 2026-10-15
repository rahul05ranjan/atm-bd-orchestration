"""One file-only sc-observability logger for each console process."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from sc_observability import Err as ObservabilityErr
from sc_observability import LogEvent, Logger, LoggerConfig, LogQuery, create_logger

from .paths import CONSOLE_NAME, console_root
from .result import Err, IoFailed, Ok, Result

EventAction = Literal["tick_start", "tick_end", "read", "github_read", "action", "hold", "error"]


class ConsoleLogger:
    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    def event(self, action: EventAction, **fields: object) -> Result[None]:
        admitted = self._logger.log(LogEvent("info", "atm_bd_console", action, fields=fields))
        if isinstance(admitted, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(admitted.error), "inspect the console log directory"))
        return Ok(None)

    def flush(self) -> Result[None]:
        flushed = self._logger.flush()
        if isinstance(flushed, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(flushed.error), "inspect the console log directory"))
        return Ok(None)

    def query(self) -> Result[tuple[Any, ...]]:
        """The stored events this console logged, each with its action and UTC timestamp."""
        queried = self._logger.query(LogQuery(service=CONSOLE_NAME))
        if isinstance(queried, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(queried.error), "inspect the console log directory"))
        return Ok(queried.value.events)


def create_console_logger(repo_root: str | Path) -> Result[ConsoleLogger]:
    # sc-observability writes its files under <log_root>/logs, so the root is the console directory.
    log_root = console_root(repo_root)
    try:
        log_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return Err(IoFailed("io_failed", str(log_root), str(error), "repair the log directory permissions"))
    created = create_logger(LoggerConfig(service=CONSOLE_NAME, log_root=str(log_root), enable_console_sink=False))
    if isinstance(created, ObservabilityErr):
        return Err(IoFailed("io_failed", str(log_root), str(created.error), "restore sc-observability"))
    return Ok(ConsoleLogger(created.value))
