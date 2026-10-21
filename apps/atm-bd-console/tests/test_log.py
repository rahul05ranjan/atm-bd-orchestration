from __future__ import annotations

import re
from typing import cast

import pytest
from sc_observability import Err as ObservabilityErr
from sc_observability import Logger, LoggerConfig, create_logger

from atm_bd_console import log
from atm_bd_console.log import create_console_logger
from atm_bd_console.result import Err, Ok

UTC_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


def test_events_are_retained_without_console_output(tmp_path, capsys) -> None:
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)
    actions = ("tick_start", "tick_end", "read", "github_read", "action", "hold", "error")
    for action in actions:
        assert created.value.event(action, source="test", ok=True) == Ok(None)
    assert created.value.flush() == Ok(None)
    queried = created.value.query()
    assert isinstance(queried, Ok)
    assert {event.action for event in queried.value} >= set(actions)
    assert all(UTC_Z.fullmatch(event.timestamp) for event in queried.value)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_event_level_and_message_are_explicit_and_default_to_info(tmp_path) -> None:
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)

    assert created.value.event("error", level="error", message="useful failure", code=1) == Ok(None)
    assert created.value.event("action", outcome="sent") == Ok(None)
    assert created.value.flush() == Ok(None)
    queried = created.value.query(levels=("error",), limit=1, order="newest_first")

    assert isinstance(queried, Ok)
    assert len(queried.value) == 1
    assert queried.value[0].level == "error"
    assert queried.value[0].message == "useful failure"
    all_events = created.value.query(order="newest_first")
    assert isinstance(all_events, Ok)
    default_event = next(event for event in all_events.value if event.action == "action")
    assert default_event.level == "info" and default_event.message is None


def test_error_message_formats_boundary_errors_as_one_line() -> None:
    assert log.error_message({
        "kind": "command_failed", "argv": ("gh", "api", "graphql", "--paginate"), "code": 1,
        "stderr": "first stderr line\nsecond stderr line", "fix": "check gh and retry",
    }) == "`check gh and retry` gh api graphql [1] first stderr line"
    assert log.error_message({
        "kind": "command_failed", "argv": ("gh", "api", "graphql"), "code": 1,
        "stderr": "", "fix": "check gh and retry",
    }).endswith("[1] no stderr")
    assert log.error_message({
        "kind": "timeout", "argv": ("gh", "api", "graphql"), "seconds": 15, "fix": "retry gh",
    }) == "`retry gh` gh api graphql timeout after 15s"
    assert log.error_message({
        "kind": "rejected", "status": 429, "message": "rate limited", "fix": "retry later",
    }) == "`retry later` rejected HTTP 429: rate limited"
    assert log.error_message({
        "kind": "parse_failed", "source": "phase config", "message": "missing root id", "fix": "restore config",
    }) == "`restore config` parse_failed phase config: missing root id"
    assert ": ;" not in log.error_message({"kind": "command_failed", "argv": (), "code": 1, "stderr": "", "fix": "retry"})


@pytest.mark.parametrize("operation", ("construction", "event", "flush"))
def test_native_typed_errors_are_exposed_as_console_errors(tmp_path, monkeypatch, operation):
    rejected = create_logger(LoggerConfig(service="", log_root=str(tmp_path)))
    assert isinstance(rejected, ObservabilityErr)

    class NativeLogger:
        def log(self, _event): return rejected
        def flush(self): return rejected

    if operation == "construction":
        monkeypatch.setattr(log, "create_logger", lambda *_: rejected)
        result = create_console_logger(tmp_path)
    else:
        logger = log.ConsoleLogger(cast(Logger, NativeLogger()))
        result = logger.event("action") if operation == "event" else logger.flush()

    assert isinstance(result, Err)
    assert result.error.kind == "io_failed"
    assert rejected.error.code in result.error.message


def test_structured_delivery_error_is_retained_by_native_logger(tmp_path):
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)
    fields = {
        "effect": "assign", "recipient": "reviewer", "task_ids": ["task-1", "task-2"],
        "outcome": "failed command_failed",
        "error": {"kind": "command_failed", "argv": ("atm", "task", "assign"), "code": 1,
                  "stderr": "assignment rejected", "fix": "retry assignment"},
    }

    assert created.value.event("action", **fields) == Ok(None)
    assert created.value.flush() == Ok(None)
    queried = created.value.query()

    assert isinstance(queried, Ok)
    assert len(queried.value) == 1
    event = queried.value[0]
    assert event.action == "action"
    assert event.fields["recipient"].value == "reviewer"
    assert [task.value for task in event.fields["task_ids"].value] == ["task-1", "task-2"]
    assert event.fields["outcome"].value == "failed command_failed"
    assert event.fields["error"].value["stderr"].value == "assignment rejected"
