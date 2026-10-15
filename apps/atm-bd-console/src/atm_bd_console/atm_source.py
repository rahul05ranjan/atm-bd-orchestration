from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .result import CommandFailed, Err, Ok, ParseFailed, Result, Timeout, Unreachable
from .types import DispatchSpec, Member, Task

TIMEOUT_S = 20


def _run(argv: list[str], stdin: str | None = None, timeout_s: int = TIMEOUT_S) -> Result[str]:
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Err(Timeout("timeout", tuple(argv), timeout_s, "retry the ATM command"))
    except (OSError, UnicodeDecodeError) as error:
        return Err(Unreachable("unreachable", tuple(argv), str(error), "install atm and check PATH"))
    if done.returncode:
        return Err(CommandFailed("command_failed", tuple(argv), done.returncode, done.stderr, "inspect ATM output"))
    return Ok(done.stdout)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def members(team: str) -> Result[tuple[Member, ...]]:
    result = _run(["atm", "members", "--team", team, "--json"])
    if isinstance(result, Err): return result
    try:
        return Ok(tuple(Member(item["name"], item.get("state") if item.get("state") in {"active", "idle", "offline", "blocked"} else "unknown", _time(item["state_changed_at"])) for item in json.loads(result.value)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm members", str(error), "repair ATM JSON"))


def tasks(team: str) -> Result[tuple[Task, ...]]:
    result = _run(["atm", "task", "list", "--all", "--json", "--team", team])
    if isinstance(result, Err): return result
    try:
        return Ok(tuple(Task(item["id"], item["agent"], item["state"], index, _time(item["assigned_at"]), _time(item["updated_at"]), item.get("description", "")) for index, item in enumerate(json.loads(result.value))))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm tasks", str(error), "repair ATM JSON"))


def send(to: str, text: str, as_: str) -> Result[None]:
    result = _run(["atm", "send", to, "--as", as_, "--stdin"], text)
    return result if isinstance(result, Err) else Ok(None)


def assign(agent: str, task_id: str, spec: DispatchSpec, as_: str) -> Result[None]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        json.dump(spec.vars, handle); vars_file = Path(handle.name)
    try:
        result = _run(["atm", "task", "assign", agent, "--as", as_, "--task-id", task_id, "--template", spec.template, "--vars", str(vars_file)])
        return result if isinstance(result, Err) else Ok(None)
    finally:
        vars_file.unlink(missing_ok=True)
