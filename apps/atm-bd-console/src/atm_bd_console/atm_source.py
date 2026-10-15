from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .result import CommandFailed, Err, IoFailed, Ok, ParseFailed, Result, Timeout, Unreachable
from .types import DispatchSpec, Member, Task

# Team resolution: `members` and `task list` read a named roster, so they pass
# `--team` explicitly. `send` takes no team here and resolves it from the invoking
# shell's ATM_TEAM when `--team` is absent (docs/requirements.md §4.1). `task assign`
# also takes no team; §4.1 has no row for it, so that resolution is unconfirmed.
TIMEOUT_S = 20
TASK_KEYS = frozenset({"id", "agent", "state", "assigned_at", "updated_at", "description"})
TASK_STATES = frozenset({"assigned", "active"})
MEMBER_STATES = frozenset({"active", "idle", "offline", "blocked"})


# Own runner, not beads_source's: this one takes an injected timeout and maps timeout and missing-binary errors, which bd's runner does not.
def _run(argv: list[str], stdin: str | None = None, timeout_s: int | None = None) -> Result[str]:
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Err(Timeout("timeout", tuple(argv), timeout_s, f"rerun `{' '.join(argv)}`, or pass a larger timeout"))
    except (OSError, UnicodeDecodeError) as error:
        return Err(Unreachable("unreachable", tuple(argv), str(error), f"install the atm CLI and put it on PATH, then rerun `{' '.join(argv)}`"))
    if done.returncode:
        return Err(CommandFailed("command_failed", tuple(argv), done.returncode, done.stderr, f"run `{' '.join(argv)}` by hand and read its stderr"))
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


def _member(item: dict, time: datetime) -> Member:
    state = item.get("state")
    return Member(item["name"], state if state in MEMBER_STATES else "unknown", time)


def _task(index: int, item: dict, assigned: datetime, updated: datetime) -> Task:
    return Task(item["id"], item["agent"], item["state"], index, assigned, updated, item["description"])


def members(team: str, *, timeout_s: int | None = None) -> Result[tuple[Member, ...]]:
    result = _run(["atm", "members", "--team", team, "--json"], timeout_s=timeout_s)
    if isinstance(result, Err):
        return result
    fix = f"rerun `atm members --team {team} --json` and check it prints a JSON list of member objects"
    try:
        items = json.loads(result.value)
        if not isinstance(items, list):
            return Err(ParseFailed("parse_failed", "atm members", "atm members must be a list", fix))
        times = tuple(_time(item["state_changed_at"]) for item in items)
        if None in times:
            return Err(ParseFailed("parse_failed", "atm members", "atm member timestamps must be ISO-8601 with a UTC offset", fix))
        return Ok(tuple(_member(item, time) for item, time in zip(items, times)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm members", str(error), fix))


def tasks(team: str, *, timeout_s: int | None = None) -> Result[tuple[Task, ...]]:
    result = _run(["atm", "task", "list", "--all", "--json", "--team", team], timeout_s=timeout_s)
    if isinstance(result, Err):
        return result
    fix = f"rerun `atm task list --all --json --team {team}` and check it prints a JSON list of task objects"
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
        return Ok(tuple(_task(index, item, *pair) for index, (item, pair) in enumerate(zip(items, times))))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return Err(ParseFailed("parse_failed", "atm task list", str(error), fix))


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
