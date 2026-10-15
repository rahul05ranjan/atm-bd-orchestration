from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from atm_bd_console import atm_source
from atm_bd_console.result import CommandFailed, Err, Ok, ParseFailed, Timeout, Unreachable
from atm_bd_console.types import DispatchSpec

FIXTURES = Path(__file__).parent / "fixtures" / "atm"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_members_maps_every_presence_and_records_presence_since(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", _fixture("members.json"))
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.members("atm-dev")

    assert isinstance(result, Ok)
    assert [(member.name, member.presence) for member in result.value] == [
        ("aria", "active"),
        ("boris", "idle"),
        ("cy", "offline"),
        ("dara", "blocked"),
        ("eli", "unknown"),
    ]
    assert result.value[3].presence_since == datetime(2026, 10, 15, 9, 3, tzinfo=UTC)
    assert fake_bin.argv() == [("atm", "members", "--team", "atm-dev", "--json")]


def test_tasks_preserve_position_and_use_updated_at_for_state_since(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", _fixture("tasks.json"))
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.tasks("atm-dev")

    assert isinstance(result, Ok)
    assert [(task.id, task.position) for task in result.value] == [
        ("atm-bh-3.group-dev", 0),
        ("atm-bh-3.group-qa", 1),
    ]
    assert result.value[0].state_since == datetime(2026, 10, 15, 9, 11, tzinfo=UTC)
    assert fake_bin.argv() == [("atm", "task", "list", "--all", "--json", "--team", "atm-dev")]


def test_send_and_assign_use_exact_argv_stdin_and_temporary_vars_file(fake_bin, monkeypatch, tmp_path) -> None:
    stdin_log = tmp_path / "stdin"
    vars_log = tmp_path / "vars.json"
    monkeypatch.setenv("FAKE_STDIN_LOG", str(stdin_log))
    monkeypatch.setenv("FAKE_VARS_LOG", str(vars_log))
    fake_bin.make(
        "atm",
        'if [ "$1" = "send" ]; then cat > "$FAKE_STDIN_LOG"; fi\n'
        'if [ "$1" = "task" ]; then for last; do :; done; cp "$last" "$FAKE_VARS_LOG"; fi\n',
    )

    sent = atm_source.send("fenix", "hello from stdin", "trinity")
    assigned = atm_source.assign(
        "aria", "atm-bh-3.group-dev", DispatchSpec("dev.md.j2", {"sprint": "bh-3", "layer": 2}), "trinity"
    )

    assert isinstance(sent, Ok) and isinstance(assigned, Ok)
    assert stdin_log.read_text() == "hello from stdin"
    assert json.loads(vars_log.read_text()) == {"sprint": "bh-3", "layer": 2}
    assert fake_bin.argv() == [
        ("atm", "send", "fenix", "--as", "trinity", "--stdin"),
        (
            "atm", "task", "assign", "aria", "--as", "trinity", "--task-id", "atm-bh-3.group-dev",
            "--template", "dev.md.j2", "--vars", fake_bin.argv()[1][-1],
        ),
    ]
    assert not Path(fake_bin.argv()[1][-1]).exists()


def test_command_failures_invalid_json_and_timeouts_are_typed_without_raising(fake_bin) -> None:
    fake_bin.make("atm", 'printf "permission denied" >&2\nexit 7\n')
    failed = atm_source.members("atm-dev")
    assert isinstance(failed, Err) and isinstance(failed.error, CommandFailed)
    assert failed.error.code == 7 and failed.error.stderr == "permission denied"

    fake_bin.make("atm", 'printf "not json"\n')
    invalid = atm_source.members("atm-dev")
    assert isinstance(invalid, Err) and isinstance(invalid.error, ParseFailed)

    fake_bin.make("atm", 'sleep 2\n')
    timed_out = atm_source._run(["atm", "wait"], timeout_s=1)
    assert isinstance(timed_out, Err) and isinstance(timed_out.error, Timeout)
    assert "raise" not in Path(atm_source.__file__).read_text()


def test_missing_atm_binary_is_a_typed_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    result = atm_source.members("atm-dev")

    assert isinstance(result, Err) and isinstance(result.error, Unreachable)
    assert result.error.argv == ("atm", "members", "--team", "atm-dev", "--json")
    assert result.error.fix == "install atm and check PATH"


def test_decode_error_is_a_typed_error(monkeypatch) -> None:
    error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(atm_source.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = atm_source._run(["atm", "members", "--json"])

    assert isinstance(result, Err) and isinstance(result.error, Unreachable)


def test_assign_with_non_serializable_vars_returns_error_and_removes_tempfile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(atm_source.tempfile, "tempdir", str(tmp_path))

    result = atm_source.assign("aria", "atm-bh-3.group-dev", DispatchSpec("dev.md.j2", {"bad": object()}), "trinity")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.fix == "use JSON-serializable template variables"
    assert list(tmp_path.iterdir()) == []


def test_public_operations_accept_an_injected_timeout(fake_bin) -> None:
    fake_bin.make("atm", "sleep 2")
    spec = DispatchSpec("dev.md.j2", {"sprint": "bh-3"})

    results = (
        atm_source.members("atm-dev", timeout_s=1),
        atm_source.tasks("atm-dev", timeout_s=1),
        atm_source.send("fenix", "hello", "trinity", timeout_s=1),
        atm_source.assign("aria", "atm-bh-3.group-dev", spec, "trinity", timeout_s=1),
    )

    assert all(isinstance(result, Err) and isinstance(result.error, Timeout) for result in results)
    assert all(result.error.seconds == 1 for result in results if isinstance(result, Err))
