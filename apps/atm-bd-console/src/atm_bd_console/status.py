from __future__ import annotations

import subprocess

from .result import CommandFailed, Error, NotFound, Timeout
from .types import Snapshot, SprintView

_COMMAND = ("mmdflux", "--format", "text")
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
        sprint_id = sprint.id.removeprefix(f"{snapshot.bead_prefix}-")
        current = next((step for step in sprint.steps if step.status == "in_progress"), None)
        if current is None and sprint.state == "ready":
            current = next((step for step in sprint.steps if step.status == "open"), None)
        line = f"{_ICONS[sprint.state]} {sprint_id}"
        if current is not None:
            line += f"  {current.kind}: {current.assignee or 'unassigned'}"
        elif sprint.depends_on:
            dependencies = " ".join(value.removeprefix(f"{snapshot.bead_prefix}-") for value in sprint.depends_on)
            line += f"  ← {dependencies}"
        lines.append(line)
    return "\n".join(lines)


def status_text(snapshot: Snapshot) -> tuple[str, Error | None]:
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
    return completed.stdout.rstrip("\n"), None
