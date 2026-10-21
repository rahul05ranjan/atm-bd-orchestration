from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atm_bd_console import atm_source
from atm_bd_console.result import CommandFailed, Err, Ok, ParseFailed, Timeout, Unreachable
from atm_bd_console.types import DispatchSpec

FIXTURES = Path(__file__).parent / "fixtures" / "atm"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_members_maps_every_presence_and_records_presence_since(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", _fixture("members.json"))
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.members("atm-dev", "trinity")

    assert isinstance(result, Ok)
    assert [(member.name, member.presence) for member in result.value] == [
        ("aria", "active"),
        ("boris", "idle"),
        ("cy", "offline"),
        ("dara", "blocked"),
        ("eli", "unknown"),
    ]
    assert result.value[3].presence_since == datetime(2026, 10, 15, 9, 3, tzinfo=UTC)
    assert (result.value[0].agent_type, result.value[0].model, result.value[0].harness) == (
        "dev", "GPT-5.6-Sol", "codex-cli",
    )
    assert result.value[2].agent_type == "sanity"
    assert fake_bin.argv() == [("atm", "members", "--team", "atm-dev", "--json")]


def test_members_accept_missing_optional_timestamps(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", '{"members": [{"name": "teammate", "state": "unknown"}]}')
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.members("atm-dev", "trinity")

    assert result == Ok((atm_source.Member("teammate", "unknown", None),))


def test_tasks_preserve_position_and_use_updated_at_for_state_since(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", _fixture("tasks.json"))
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.tasks("atm-dev", "trinity")

    assert isinstance(result, Ok)
    assert [(task.id, task.agent, task.position) for task in result.value] == [
        ("CI-1844-lint", "arch-ctm", 0),
        ("atm-bh-3.f4-r2-fix", "cipher", 1),
    ]
    assert result.value[0].state_since == datetime(2026, 10, 15, 4, 17, 56, 150697, tzinfo=UTC)
    assert fake_bin.argv() == [("atm", "task", "list", "--all", "--json", "--team", "atm-dev", "--as", "trinity")]


def test_task_closed_reads_the_last_state_transition_of_the_task_history(fake_bin, monkeypatch) -> None:
    closed = _fixture("task-events-closed.json")
    reopened = json.loads(closed)
    reopened["events"].append({**reopened["events"][0], "seq": 5, "event": "reopened", "from_state": "complete"})
    for body, expected in ((closed, True), (json.dumps(reopened), False), ('{"events": [], "handoffs": []}', False)):
        monkeypatch.setenv("ATM_BODY", body)
        fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

        assert atm_source.task_closed("atm-bh-6.group-qa", "atm-dev", "trinity") == Ok(expected)
    assert fake_bin.argv()[-1] == (
        "atm", "task", "events", "atm-bh-6.group-qa", "--all", "--json", "--team", "atm-dev", "--as", "trinity",
    )


@pytest.mark.parametrize("body", ('[]', '{"events": {}}', '{"events": [1]}', 'not json'))
def test_task_closed_rejects_a_malformed_history(fake_bin, monkeypatch, body: str) -> None:
    monkeypatch.setenv("ATM_BODY", body)
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.task_closed("t", "atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.source == "atm task events"


def test_tasks_rejects_a_complete_state_as_unknown(fake_bin, monkeypatch) -> None:
    # Closed rows never appear in `atm task list` output (its help: the caller's open tasks), so a complete state is an unknown state here.
    body = '[{"task_id": "t", "assignee": "aria", "state": "complete", "assigned_at": "2026-10-15T09:10:00Z", "updated_at": "2026-10-15T09:11:00Z", "description": ""}]'
    monkeypatch.setenv("ATM_BODY", body)
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.tasks("atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.source == "atm task list"


@pytest.mark.parametrize(
    ("command", "body", "source"),
    [
        ("members", '{"members": [{"name": "aria", "state": "active", "state_changed_at": "2026-10-15T09:00:00"}]}', "atm members"),
        ("members", '[{"name": "aria", "state": "active", "state_changed_at": "2026-10-15T09:00:00Z"}]', "atm members"),
        ("tasks", '[{"task_id": "t", "assignee": "aria", "state": "assigned", "assigned_at": "2026-10-15T09:10:00", "updated_at": "2026-10-15T09:11:00Z", "description": ""}]', "atm task list"),
        ("tasks", '[{"task_id": "t", "assignee": "aria", "state": "assigned", "assigned_at": "2026-10-15T09:10:00Z", "updated_at": null, "description": ""}]', "atm task list"),
    ],
)
def test_naive_null_and_non_list_timestamps_are_rejected_not_read_as_local(fake_bin, monkeypatch, command, body, source) -> None:
    monkeypatch.setenv("ATM_BODY", body)
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.members("atm-dev", "trinity") if command == "members" else atm_source.tasks("atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.source == source


def test_err_fixes_name_the_command_to_run(fake_bin, monkeypatch) -> None:
    fake_bin.make("atm", "exit 3\n")
    failed = atm_source.members("atm-dev", "trinity")
    monkeypatch.setenv("ATM_BODY", "{}")
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')
    parse_failed = atm_source.tasks("atm-dev", "trinity")

    assert isinstance(failed, Err) and "atm members --team atm-dev --json" in failed.error.fix
    assert isinstance(parse_failed, Err) and "atm task list --all --json --team atm-dev --as trinity" in parse_failed.error.fix


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

    sent = atm_source.send("fenix", "hello from stdin", "trinity", "atm-dev")
    assigned = atm_source.assign(
        "aria", "atm-bh-3.group-dev", DispatchSpec("dev.md.j2", {"sprint": "bh-3", "layer": 2}), "trinity", "atm-dev"
    )

    assert isinstance(sent, Ok) and isinstance(assigned, Ok)
    assert stdin_log.read_text() == "hello from stdin"
    assert json.loads(vars_log.read_text()) == {"sprint": "bh-3", "layer": 2}
    assert fake_bin.argv() == [
        ("atm", "send", "fenix", "--as", "trinity", "--team", "atm-dev", "--stdin"),
        (
            "atm", "task", "assign", "aria", "--as", "trinity", "--team", "atm-dev", "--task-id", "atm-bh-3.group-dev",
            "--template", "dev.md.j2", "--vars", fake_bin.argv()[1][-1],
        ),
    ]
    assert not Path(fake_bin.argv()[1][-1]).exists()


def test_command_failures_invalid_json_and_timeouts_are_typed_without_raising(fake_bin, monkeypatch) -> None:
    fake_bin.make("atm", 'printf "permission denied" >&2\nexit 7\n')
    failed = atm_source.members("atm-dev", "trinity")
    assert isinstance(failed, Err) and isinstance(failed.error, CommandFailed)
    assert failed.error.code == 7 and failed.error.stderr == "permission denied"

    fake_bin.make("atm", 'printf "not json"\n')
    invalid = atm_source.members("atm-dev", "trinity")
    assert isinstance(invalid, Err) and isinstance(invalid.error, ParseFailed)

    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    timed_out = atm_source._run(["atm", "wait"], "trinity", timeout_s=1)
    assert isinstance(timed_out, Err) and isinstance(timed_out.error, Timeout)


def test_missing_atm_binary_is_a_typed_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    result = atm_source.members("atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, Unreachable)
    assert result.error.argv == ("atm", "members", "--team", "atm-dev", "--json")
    assert result.error.fix == "install the atm CLI and put it on PATH, then rerun `atm members --team atm-dev --json`"


def test_decode_error_is_the_runners_command_failed_without_exit_code(monkeypatch) -> None:
    error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = atm_source._run(["atm", "members", "--json"], "trinity")

    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert result.error.code == -1


def test_child_runs_as_the_configured_identity(fake_bin) -> None:
    fake_bin.make("atm", 'printf "%s" "$ATM_IDENTITY"\n')

    assert atm_source._run(["atm", "whoami"], "trinity") == Ok("trinity")


def test_assign_with_non_serializable_vars_returns_error_and_removes_tempfile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(atm_source.tempfile, "tempdir", str(tmp_path))

    result = atm_source.assign("aria", "atm-bh-3.group-dev", DispatchSpec("dev.md.j2", {"bad": object()}), "trinity", "atm-dev")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.fix == "use JSON-serializable template variables"
    assert list(tmp_path.iterdir()) == []


def test_public_operations_accept_an_injected_timeout(monkeypatch) -> None:
    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    spec = DispatchSpec("dev.md.j2", {"sprint": "bh-3"})

    results = (
        atm_source.members("atm-dev", "trinity", timeout_s=1),
        atm_source.tasks("atm-dev", "trinity", timeout_s=1),
        atm_source.send("fenix", "hello", "trinity", "atm-dev", timeout_s=1),
        atm_source.assign("aria", "atm-bh-3.group-dev", spec, "trinity", "atm-dev", timeout_s=1),
    )

    assert all(isinstance(result, Err) and isinstance(result.error, Timeout) for result in results)
    assert all(result.error.seconds == 1 for result in results
               if isinstance(result, Err) and isinstance(result.error, Timeout))


@pytest.mark.parametrize(
    "call",
    [
        lambda team: atm_source.members(team, "trinity"),
        lambda team: atm_source.tasks(team, "trinity"),
        lambda team: atm_source.send("fenix", "hello", "trinity", team),
        lambda team: atm_source.assign("aria", "atm-bh-3.group-dev", DispatchSpec("dev.md.j2", {"sprint": "bh-3"}), "trinity", team),
    ],
    ids=["members", "tasks", "send", "assign"],
)
def test_every_atm_entry_point_passes_the_explicit_team(fake_bin, call) -> None:
    fake_bin.make("atm", "printf '[]'\n")

    call("atm-dev")

    argv = fake_bin.argv()
    assert argv
    assert all(command[command.index("--team") + 1] == "atm-dev" for command in argv)


def test_bare_list_members_payload_is_rejected_with_the_members_list_message(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", '[{"name": "aria", "state": "active"}]')
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')

    result = atm_source.members("atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.message == "atm members must contain a members list"


def test_every_atm_call_runs_as_the_lead_without_a_shell_identity(fake_bin, monkeypatch, tmp_path) -> None:
    # Observed 2026-10-18 on phase bi: a shell without ATM_IDENTITY made `atm members` and `atm task list`
    # exit 3 "identity is not configured". The fake atm refuses exactly like the real CLI.
    identity_log = tmp_path / "identity.log"
    monkeypatch.delenv("ATM_IDENTITY", raising=False)
    monkeypatch.setenv("FAKE_IDENTITY_LOG", str(identity_log))
    fake_bin.make(
        "atm",
        '[ -n "$ATM_IDENTITY" ] || { printf "identity is not configured" >&2; exit 3; }\n'
        'printf "%s\\n" "$ATM_IDENTITY" >> "$FAKE_IDENTITY_LOG"\n'
        'case "$1" in members) printf \'{"members": []}\';; task) printf "[]";; esac\n',
    )

    members = atm_source.members("atm-dev", "trinity")
    tasks = atm_source.tasks("atm-dev", "trinity")
    sent = atm_source.send("fenix", "hello", "trinity", "atm-dev")

    assert (members, tasks, sent) == (Ok(()), Ok(()), Ok(None))
    assert identity_log.read_text().split() == ["trinity", "trinity", "trinity"]
    assert fake_bin.argv()[1] == ("atm", "task", "list", "--all", "--json", "--team", "atm-dev", "--as", "trinity")


def test_command_failed_fix_names_the_identity_the_console_ran_as(fake_bin) -> None:
    fake_bin.make("atm", 'printf "identity is not configured" >&2\nexit 3\n')

    result = atm_source.members("atm-dev", "trinity")

    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert result.error.fix == (
        "rerun `atm members --team atm-dev --json` as ATM_IDENTITY=trinity and read its stderr, or pass a larger timeout"
    )
