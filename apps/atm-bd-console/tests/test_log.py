from __future__ import annotations

from sc_observability import LogQuery

from atm_bd_console.log import create_console_logger
from atm_bd_console.result import Ok


def test_events_are_retained_without_console_output(tmp_path, capsys) -> None:
    created = create_console_logger(tmp_path)
    assert isinstance(created, Ok)
    actions = ("tick_start", "tick_end", "read", "github_read", "action", "hold", "error")
    for action in actions:
        assert created.value.event(action, source="test", ok=True) == Ok(None)
    assert isinstance(created.value.native.flush(), object)
    queried = created.value.native.query(LogQuery(service="atm-bd-console"))
    assert {event.action for event in queried.value.events} >= set(actions)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
