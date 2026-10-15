from __future__ import annotations

import re

from sc_observability import LogQuery

from atm_bd_console.log import create_console_logger
from atm_bd_console.result import Ok

UTC_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


def test_events_are_retained_without_console_output(tmp_path, capsys) -> None:
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)
    actions = ("tick_start", "tick_end", "read", "github_read", "action", "hold", "error")
    for action in actions:
        assert created.value.event(action, source="test", ok=True) == Ok(None)
    flushed = created.value.native.flush()
    assert flushed.kind == "ok" and flushed.value.kind == "completed"
    queried = created.value.native.query(LogQuery(service="atm-bd-console"))
    assert {event.action for event in queried.value.events} >= set(actions)
    assert all(UTC_Z.fullmatch(event.timestamp) for event in queried.value.events)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
