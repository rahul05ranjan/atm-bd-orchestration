from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .result import CommandFailed, Err, IoFailed, Ok, ParseFailed, Result, Timeout, Unreachable
from .types import DispatchSpec, Member, Task

TIMEOUT_S = 20
TASK_KEYS = frozenset({"id", "agent", "state", "assigned_at", "updated_at", "description"})
TASK_STATES = frozenset({"assigned", "active"})


def _run(argv: list[str], stdin: str | None = None, timeout_s: int | None = None) -> Result[str]:
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
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


def members(team: str, *, timeout_s: int | None = None) -> Result[tuple[Member, ...]]:
    result = _run(["atm", "members", "--team", team, "--json"], timeout_s=timeout_s)
    if isinstance(result, Err): return result
    try:
        return Ok(tuple(Member(item["name"], item.get("state") if item.get("state") in {"active", "idle", "offline", "blocked"} else "unknown", _time(item["state_changed_at"])) for item in json.loads(result.value)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm members", str(error), "repair ATM JSON"))


def tasks(team: str, *, timeout_s: int | None = None) -> Result[tuple[Task, ...]]:
    result = _run(["atm", "task", "list", "--all", "--json", "--team", team], timeout_s=timeout_s)
    if isinstance(result, Err): return result
    try:
        items = json.loads(result.value)
        if not isinstance(items, list):
            return Err(ParseFailed("parse_failed", "atm tasks", "atm tasks must be a list", "repair ATM JSON"))
        if any(not isinstance(item, dict) or not TASK_KEYS <= item.keys() for item in items):
            return Err(ParseFailed("parse_failed", "atm tasks", f"atm tasks must include {sorted(TASK_KEYS)}", "repair ATM JSON"))
        if any(item["state"] not in TASK_STATES for item in items):
            return Err(ParseFailed("parse_failed", "atm tasks", f"atm task states must be one of {sorted(TASK_STATES)}", "repair ATM JSON"))
        return Ok(tuple(Task(item["id"], item["agent"], item["state"], index, _time(item["assigned_at"]), _time(item["updated_at"]), item["description"]) for index, item in enumerate(items)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm tasks", str(error), "repair ATM JSON"))


def send(to: str, text: str, as_: str, *, timeout_s: int | None = None) -> Result[None]:
    result = _run(["atm", "send", to, "--as", as_, "--stdin"], text, timeout_s)
    return result if isinstance(result, Err) else Ok(None)


def assign(agent: str, task_id: str, spec: DispatchSpec, as_: str, *, timeout_s: int | None = None) -> Result[None]:
    vars_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            vars_file = Path(handle.name)
            json.dump(spec.vars, handle)
        result = _run(["atm", "task", "assign", agent, "--as", as_, "--task-id", task_id, "--template", spec.template, "--vars", str(vars_file)], timeout_s=timeout_s)
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
