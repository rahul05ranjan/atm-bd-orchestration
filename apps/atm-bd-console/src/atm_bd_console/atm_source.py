from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .enums import SprintState
from .result import CommandFailed, Err, IoFailed, Ok, ParseFailed, Result, Timeout, Unreachable
from .types import DispatchSpec, Member, Task

# Team and identity resolution: every command passes `--team` from Config.team and runs as
# Config.lead (`ATM_IDENTITY` in the subprocess environment, `--as` where the subcommand takes
# it), so no command reads either from the launching shell. `atm members` has no `--as`, and
# a shell without `ATM_IDENTITY` made every roster read fail with "identity is not configured".
TIMEOUT_S = 20
TASK_KEYS = frozenset({"task_id", "assignee", "state", "assigned_at", "updated_at", "description"})
TASK_STATES = frozenset({"assigned", "active"})
MEMBER_STATES = frozenset({"active", "idle", "offline", "blocked"})


def sprint_state_of(raw: str) -> SprintState:
    try:
        return SprintState(raw)
    except ValueError:
        return SprintState.UNKNOWN


# Own runner: ATM sends stdin text and maps missing binaries or undecodable output to Unreachable.
def _run(argv: list[str], as_: str, stdin: str | None = None, timeout_s: int | None = None) -> Result[str]:
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    env = {**os.environ, "ATM_IDENTITY": as_}
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return Err(Timeout("timeout", tuple(argv), timeout_s, f"rerun `{' '.join(argv)}`, or pass a larger timeout"))
    except (OSError, UnicodeDecodeError) as error:
        return Err(Unreachable("unreachable", tuple(argv), str(error), f"install the atm CLI and put it on PATH, then rerun `{' '.join(argv)}`"))
    if done.returncode:
        return Err(CommandFailed("command_failed", tuple(argv), done.returncode, done.stderr, f"run `ATM_IDENTITY={as_} {' '.join(argv)}` by hand and read its stderr"))
    return Ok(done.stdout)


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
    result = _run(["atm", "members", "--team", team, "--json"], as_, timeout_s=timeout_s)
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
    result = _run(["atm", "task", "list", "--all", "--json", "--team", team, "--as", as_], as_, timeout_s=timeout_s)
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


def send(to: str, text: str, as_: str, team: str, *, timeout_s: int | None = None) -> Result[None]:
    result = _run(["atm", "send", to, "--as", as_, "--team", team, "--stdin"], as_, text, timeout_s)
    return result if isinstance(result, Err) else Ok(None)


def assign(agent: str, task_id: str, spec: DispatchSpec, as_: str, team: str, *, timeout_s: int | None = None) -> Result[None]:
    vars_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            vars_file = Path(handle.name)
            json.dump(spec.vars, handle)
        result = _run(["atm", "task", "assign", agent, "--as", as_, "--team", team, "--task-id", task_id, "--template", spec.template, "--vars", str(vars_file)], as_, timeout_s=timeout_s)
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
