from __future__ import annotations

import re

import pytest
from sc_observability import Err as ObservabilityErr
from sc_observability import LoggerConfig, create_logger

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
        logger = log.ConsoleLogger(NativeLogger())
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
