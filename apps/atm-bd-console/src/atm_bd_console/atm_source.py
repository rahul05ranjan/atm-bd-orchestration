from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import command
from .enums import SprintState
from .result import CommandFailed, Err, IoFailed, Ok, ParseFailed, Result, Unreachable
from .types import DispatchSpec, Member, Task

# The caller supplies the identity and team for each command. The subprocess environment
# and --as agree; the runner uses the lead for reads/assignments and the notification sender
# for plain sends. `atm members` has no --as and still needs ATM_IDENTITY in its environment.
TIMEOUT_S = 20
TASK_KEYS = frozenset({"task_id", "assignee", "state", "assigned_at", "updated_at", "description"})
TASK_STATES = frozenset({"assigned", "active"})
# `atm task list` shows open tasks only; a closed task is seen in its history, whose last event moves it to this state.
CLOSED_TASK_STATE = "complete"
MEMBER_STATES = frozenset({"active", "idle", "offline", "blocked"})


def sprint_state_of(raw: str) -> SprintState:
    try:
        return SprintState(raw)
    except ValueError:
        return SprintState.UNKNOWN


# ATM runs through command.run as ATM_IDENTITY=<as_>; a missing atm binary is the one failure this
# module names differently (Unreachable with the install fix), every other failure keeps command.run's shape.
def _run(argv: list[str], as_: str, stdin: str | None = None, timeout_s: int | None = None, *, team: str | None = None) -> Result[str]:
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    command_line = " ".join(argv)
    result = command.run(
        argv, f"rerun `{command_line}` as ATM_IDENTITY={as_} and read its stderr, or pass a larger timeout",
        timeout_s=timeout_s, stdin=stdin, env={**os.environ, "ATM_IDENTITY": as_, **({"ATM_TEAM": team} if team is not None else {})},
    )
    if isinstance(result, Err) and isinstance(result.error, CommandFailed) and result.error.code == command.MISSING_PROGRAM_CODE:
        return Err(Unreachable("unreachable", tuple(argv), result.error.stderr, f"install the atm CLI and put it on PATH, then rerun `{command_line}`"))
    if isinstance(result, Err):
        return result
    return Ok(result.value.stdout)


def _time(value: object) -> datetime | None:
    # ATM stamps are ISO-8601 with an offset; a naive or null stamp is rejected, never read as local time.
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _member(item: dict, time: datetime | None) -> Member:
    state = item.get("state")
    return Member(
        item["name"],
        state if state in MEMBER_STATES else "unknown",
        time,
        *(item.get(field) if isinstance(item.get(field), str) else None for field in ("agent_type", "model", "harness")),
    )


def _task(index: int, item: dict, assigned: datetime, updated: datetime) -> Task:
    return Task(item["task_id"], item["assignee"], item["state"], index, assigned, updated, item["description"])


def members(team: str, as_: str, *, timeout_s: int | None = None) -> Result[tuple[Member, ...]]:
    result = _run(["atm", "members", "--team", team, "--json"], as_, timeout_s=timeout_s, team=team)
    if isinstance(result, Err):
        return result
    fix = f"rerun `atm members --team {team} --json` and check it prints a JSON object with a members list"
    try:
        body = json.loads(result.value)
        items = body.get("members") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return Err(ParseFailed("parse_failed", "atm members", "atm members must contain a members list", fix))
        raw_times = tuple(item.get("state_changed_at") for item in items)
        times = tuple(_time(time) for time in raw_times)
        if any(raw_time is not None and time is None for raw_time, time in zip(raw_times, times)):
            return Err(ParseFailed("parse_failed", "atm members", "atm member timestamps must be ISO-8601 with a UTC offset", fix))
        return Ok(tuple(_member(item, time) for item, time in zip(items, times)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm members", str(error), fix))


def tasks(team: str, as_: str, *, timeout_s: int | None = None) -> Result[tuple[Task, ...]]:
    result = _run(["atm", "task", "list", "--all", "--json", "--team", team, "--as", as_], as_, timeout_s=timeout_s, team=team)
    if isinstance(result, Err):
        return result
    fix = f"rerun `atm task list --all --json --team {team} --as {as_}` and check it prints a JSON list of task objects"
    try:
        items = json.loads(result.value)
        if not isinstance(items, list):
            return Err(ParseFailed("parse_failed", "atm task list", "atm task list must be a list", fix))
        if any(not isinstance(item, dict) or not TASK_KEYS <= item.keys() for item in items):
            return Err(ParseFailed("parse_failed", "atm task list", f"atm task list must include {sorted(TASK_KEYS)}", fix))
        if any(item["state"] not in TASK_STATES for item in items):
            return Err(ParseFailed("parse_failed", "atm task list", f"atm task states must be one of {sorted(TASK_STATES)}", fix))
        times = tuple((_time(item["assigned_at"]), _time(item["updated_at"])) for item in items)
        if any(None in pair for pair in times):
            return Err(ParseFailed("parse_failed", "atm task list", "atm task timestamps must be ISO-8601 with a UTC offset", fix))
        return Ok(tuple(_task(index, item, assigned, updated)
                        for index, (item, (assigned, updated)) in enumerate(zip(items, times))
                        if assigned is not None and updated is not None))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm task list", str(error), fix))


def task_closed(task_id: str, team: str, as_: str, *, timeout_s: int | None = None) -> Result[bool]:
    """Whether the ATM task `task_id` exists and its last state transition closed it. An id with no task has an empty
    history and is not closed."""
    argv = ["atm", "task", "events", task_id, "--all", "--json", "--team", team, "--as", as_]
    result = _run(argv, as_, timeout_s=timeout_s, team=team)
    if isinstance(result, Err):
        return result
    fix = f"rerun `{' '.join(argv)}` and check it prints a JSON object with an events list"
    try:
        body = json.loads(result.value)
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            return Err(ParseFailed("parse_failed", "atm task events", "atm task events must contain an events list of objects", fix))
        # Reminder and nudge events carry no to_state; the task's state is the last transition's to_state.
        states = [event["to_state"] for event in events if event.get("to_state") is not None]
        return Ok(bool(states) and states[-1] == CLOSED_TASK_STATE)
    except json.JSONDecodeError as error:
        return Err(ParseFailed("parse_failed", "atm task events", str(error), fix))


def send(to: str, text: str, as_: str, team: str, *, timeout_s: int | None = None) -> Result[None]:
    result = _run(["atm", "send", to, "--as", as_, "--team", team, "--stdin"], as_, text, timeout_s, team=team)
    return result if isinstance(result, Err) else Ok(None)


def assign(agent: str, task_id: str, spec: DispatchSpec, as_: str, team: str, *, timeout_s: int | None = None) -> Result[None]:
    vars_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            vars_file = Path(handle.name)
            json.dump(spec.vars, handle)
        result = _run(["atm", "task", "assign", agent, "--as", as_, "--team", team, "--task-id", task_id, "--template", spec.template, "--vars", str(vars_file)], as_, timeout_s=timeout_s, team=team)
        return result if isinstance(result, Err) else Ok(None)
    except (TypeError, ValueError) as error:
        return Err(ParseFailed("parse_failed", "dispatch vars", str(error), "use JSON-serializable template variables"))
    except OSError as error:
        return Err(IoFailed("io_failed", str(vars_file or "<temporary file>"), str(error), "check temporary directory permissions"))
    finally:
        if vars_file is not None:
            try:
                vars_file.unlink(missing_ok=True)
            except OSError:
                pass
