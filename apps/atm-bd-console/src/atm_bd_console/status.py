from __future__ import annotations

import re
import subprocess

from .ids import short_id
from .result import CommandFailed, ConfigInvalid, Error, NotFound, Timeout
from .types import Snapshot, SprintView

_COMMAND = ("mmdflux", "--format", "text")
_VERSION_COMMAND = ("mmdflux", "--version")
_MINIMUM_VERSION = (2, 6, 1)
_VERSION_PATTERN = re.compile(r"\b([0-9]+)\.([0-9]+)\.([0-9]+)\b")
_ICONS = {"closed": "✅", "in_progress": "🌀", "ready": "📥", "not_ready": "⏳"}


def _fallback(snapshot: Snapshot) -> str:
    by_id = {sprint.id: sprint for sprint in snapshot.sprints}

    def depth(sprint: SprintView, seen: frozenset[str] = frozenset()) -> int:
        if sprint.id in seen:
            return 0
        parents = (by_id[dependency] for dependency in sprint.depends_on if dependency in by_id)
        return 1 + max((depth(parent, seen | {sprint.id}) for parent in parents), default=-1)

    ordered = sorted(enumerate(snapshot.sprints), key=lambda item: (depth(item[1]), item[0]))
    lines: list[str] = []
    for _, sprint in ordered:
        sprint_id = short_id(sprint.id, snapshot.bead_prefix)
        current = next((step for step in sprint.steps if step.status == "in_progress"), None)
        if current is None and sprint.state == "ready":
            current = next((step for step in sprint.steps if step.status == "open"), None)
        line = f"{_ICONS[sprint.state]} {sprint_id}"
        if current is not None:
            line += f"  {current.kind}: {current.assignee or 'unassigned'}"
        elif sprint.depends_on:
            dependencies = " ".join(short_id(value, snapshot.bead_prefix) for value in sprint.depends_on)
            line += f"  ← {dependencies}"
        lines.append(line)
    return "\n".join(lines)


def status_text(snapshot: Snapshot) -> tuple[str, Error | None]:
    try:
        version = subprocess.run(
            _VERSION_COMMAND, capture_output=True, text=True, timeout=20, check=False,
        )
    except FileNotFoundError:
        return _fallback(snapshot), NotFound("not_found", "mmdflux", "install mmdflux >=2.6.1")
    except subprocess.TimeoutExpired:
        return _fallback(snapshot), Timeout("timeout", _VERSION_COMMAND, 20, "retry or simplify the status diagram")
    except OSError as error:
        return _fallback(snapshot), CommandFailed(
            "command_failed", _VERSION_COMMAND, -1, str(error), "check the mmdflux installation",
        )

    reported = (version.stdout or version.stderr).strip()
    match = _VERSION_PATTERN.search(reported)
    if version.returncode != 0:
        return _fallback(snapshot), CommandFailed(
            "command_failed", _VERSION_COMMAND, version.returncode, version.stderr,
            "install or upgrade mmdflux >=2.6.1",
        )
    actual = tuple(int(part) for part in match.groups()) if match else None
    if actual is None or actual < _MINIMUM_VERSION:
        return _fallback(snapshot), ConfigInvalid(
            "config_invalid", "mmdflux_version", reported or "unknown",
            "install or upgrade mmdflux >=2.6.1",
        )

    try:
        completed = subprocess.run(
            _COMMAND, input=snapshot.mermaid, capture_output=True, text=True, timeout=20, check=False,
        )
    except FileNotFoundError:
        return _fallback(snapshot), NotFound("not_found", "mmdflux", "install mmdflux >=2.6.1")
    except subprocess.TimeoutExpired:
        return _fallback(snapshot), Timeout("timeout", _COMMAND, 20, "retry or simplify the status diagram")
    except OSError as error:
        return _fallback(snapshot), CommandFailed(
            "command_failed", _COMMAND, -1, str(error), "check the mmdflux installation",
        )
    if completed.returncode != 0:
        return _fallback(snapshot), CommandFailed(
            "command_failed", _COMMAND, completed.returncode, completed.stderr,
            "correct the Mermaid source or mmdflux installation",
        )
    return completed.stdout, None
