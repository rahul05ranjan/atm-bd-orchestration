"""One file-only sc-observability logger for each console process."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import Any, Literal, TypeVar

from sc_observability import Err as ObservabilityErr
from sc_observability import Level, LogEvent, Logger, LoggerConfig, LogQuery, create_logger

from .paths import CONSOLE_NAME, console_root
from .result import Err, Error, IoFailed, Ok, Result

EventAction = Literal["tick_start", "tick_end", "read", "github_read", "action", "hold", "error"]
Value = TypeVar("Value")


class ConsoleLogger:
    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    def event(
        self,
        action: EventAction,
        *,
        level: Level = "info",
        message: str | None = None,
        **fields: object,
    ) -> Result[None]:
        admitted = self._logger.log(LogEvent(level, "atm_bd_console", action, message=message, fields=fields))
        if isinstance(admitted, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(admitted.error), "inspect the console log directory"))
        return Ok(None)

    def flush(self) -> Result[None]:
        flushed = self._logger.flush()
        if isinstance(flushed, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(flushed.error), "inspect the console log directory"))
        return Ok(None)

    def query(
        self,
        *,
        levels: tuple[Level, ...] = (),
        limit: int = 100,
        order: Literal["oldest_first", "newest_first"] = "oldest_first",
    ) -> Result[tuple[Any, ...]]:
        """The stored events this console logged, each with its action and UTC timestamp."""
        queried = self._logger.query(LogQuery(service=CONSOLE_NAME, levels=levels, limit=limit, order=order))
        if isinstance(queried, ObservabilityErr):
            return Err(IoFailed("io_failed", "logs", str(queried.error), "inspect the console log directory"))
        return Ok(queried.value.events)


def error_message(error: Mapping[str, object]) -> str:
    """Compose one useful, single-line message from a structured boundary error."""
    kind = str(error.get("kind", "error"))
    fix = " ".join(str(error.get("fix", "")).split())
    prefix = f"`{fix}` " if fix else ""
    argv_value = error.get("argv", ())
    argv = tuple(str(part) for part in argv_value) if isinstance(argv_value, (list, tuple)) else ()
    if kind == "command_failed":
        command = _command_words(argv)
        stderr_lines = str(error.get("stderr", "")).splitlines()
        detail = next((" ".join(line.split()) for line in stderr_lines if line.strip()), "no stderr")
        command_detail = f"{command} " if command else ""
        return f"{prefix}{command_detail}[{error.get('code', '?')}] {detail}"
    if kind == "timeout":
        command = " ".join(" ".join(part.split()) for part in argv)
        command_detail = f"{command} " if command else ""
        return f"{prefix}{command_detail}timeout after {error.get('seconds', '?')}s"
    if kind == "parse_failed":
        source = " ".join(str(error.get("source", "")).split())
        message = " ".join(str(error.get("message", "")).split())
        detail = f"{source}: {message}" if source and message else source or message
        return f"{prefix}{kind}" + (f" {detail}" if detail else "")
    detail_value = next((error[key] for key in ("message", "what", "path", "key", "source") if error.get(key)), None)
    detail = " ".join(str(detail_value).split()) if detail_value is not None else None
    if kind == "rejected" and error.get("status") is not None:
        detail = f"HTTP {error['status']}" + (f": {detail}" if detail else "")
    return f"{prefix}{kind}" + (f" {detail}" if detail else "")


def _command_words(argv: tuple[str, ...]) -> str:
    words = []
    for part in argv:
        if part.startswith("-"):
            break
        words.append(part)
    return " ".join(" ".join(word.split()) for word in words)


class TickLog:
    """Collect logging failures without changing the outcome of business effects."""

    def __init__(self, logger: Result[ConsoleLogger]) -> None:
        self.logger = logger
        self.errors: list[Error] = [logger.error] if isinstance(logger, Err) else []
        self._reported_errors: set[Error] = set()

    def mark_reported(self, error: Error) -> None:
        self._reported_errors.add(error)

    def error(self, error: Error, *, source: str | None = None) -> None:
        if error in self._reported_errors:
            return
        self.mark_reported(error)
        self.event(
            "error", source=source, level="error", message=error_message(asdict(error)), error=asdict(error),
        )

    def event(
        self,
        action: EventAction,
        *,
        level: Level = "info",
        message: str | None = None,
        **fields: object,
    ) -> None:
        if isinstance(self.logger, Ok):
            admitted = self.logger.value.event(action, level=level, message=message, **fields)
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
        if isinstance(result, Err):
            self.mark_reported(result.error)
        self.event(
            "read", source=source, outcome="failed" if isinstance(result, Err) else "ok",
            level="error" if isinstance(result, Err) else "info",
            message=error_message(asdict(result.error)) if isinstance(result, Err) else None,
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
