"""One file-only sc-observability logger for each console process."""
from __future__ import annotations

from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import Any, Literal, TypeVar

from sc_observability import Err as ObservabilityErr
from sc_observability import LogEvent, Logger, LoggerConfig, LogQuery, create_logger

from .paths import CONSOLE_NAME, console_root
from .result import Err, Error, IoFailed, Ok, Result

EventAction = Literal["tick_start", "tick_end", "read", "github_read", "action", "hold", "error"]
Value = TypeVar("Value")


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


class TickLog:
    """Collect logging failures without changing the outcome of business effects."""

    def __init__(self, logger: Result[ConsoleLogger]) -> None:
        self.logger = logger
        self.errors: list[Error] = [logger.error] if isinstance(logger, Err) else []

    def event(self, action: EventAction, **fields: object) -> None:
        if isinstance(self.logger, Ok):
            admitted = self.logger.value.event(action, **fields)
            if isinstance(admitted, Err):
                self.errors.append(admitted.error)

    def finish(self, actions: int) -> None:
        self.event("tick_end", actions=actions)
        self.flush()

    def flush(self) -> None:
        if isinstance(self.logger, Ok):
            flushed = self.logger.value.flush()
            if isinstance(flushed, Err):
                self.errors.append(flushed.error)

    def result(self, source: str, result: Result[Value], **fields: object) -> Result[Value]:
        self.event(
            "read", source=source, outcome="failed" if isinstance(result, Err) else "ok",
            error=asdict(result.error) if isinstance(result, Err) else None, **fields,
        )
        return result


def create_console_logger(repo_root: str | Path) -> Result[ConsoleLogger]:
    """Reuse the process-owned logger for this repository across every tick."""
    log_root = console_root(repo_root)
    try:
        return _process_logger(log_root.resolve())
    except (OSError, RuntimeError) as error:
        return Err(IoFailed("io_failed", str(log_root), str(error), "repair the log directory path"))


@cache
def _process_logger(log_root: Path) -> Result[ConsoleLogger]:
    # sc-observability writes its files under <log_root>/logs, so the root is the console directory.
    try:
        log_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return Err(IoFailed("io_failed", str(log_root), str(error), "repair the log directory permissions"))
    created = create_logger(LoggerConfig(service=CONSOLE_NAME, log_root=str(log_root), enable_console_sink=False))
    if isinstance(created, ObservabilityErr):
        return Err(IoFailed("io_failed", str(log_root), str(created.error), "restore sc-observability"))
    return Ok(ConsoleLogger(created.value))
