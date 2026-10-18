from __future__ import annotations

import re

from . import command
from .enums import SprintState
from .icons import SPRINT_ICONS
from .ids import short_id
from .render import render_agent
from .result import CommandFailed, ConfigInvalid, Err, Error, NotFound
from .types import Snapshot, SprintView

_COMMAND = ("mmdflux", "--format", "text")
_VERSION_COMMAND = ("mmdflux", "--version")
_MINIMUM_VERSION = (2, 6, 1)
_VERSION_PATTERN = re.compile(r"\b([0-9]+)\.([0-9]+)\.([0-9]+)\b")
_VERSION_FIX = "install or upgrade mmdflux >=2.6.1"
_RENDER_FIX = "correct the Mermaid source or mmdflux installation, or retry with a simpler status diagram"


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
        current = next((step for step in sprint.steps if step.status == SprintState.IN_PROGRESS), None)
        if current is None and sprint.state == SprintState.READY:
            current = next((step for step in sprint.steps if step.status == SprintState.OPEN), None)
        line = f"{SPRINT_ICONS[sprint.state][0]} {sprint_id}"
        if current is not None:
            member = next((agent for agent in snapshot.agents if agent.name == current.assignee), None)
            assignee = render_agent(current.assignee, member).plain
            line += f"  {current.kind}: {assignee}"
        elif sprint.depends_on:
            dependencies = " ".join(short_id(value, snapshot.bead_prefix) for value in sprint.depends_on)
            line += f"  ← {dependencies}"
        lines.append(line)
    return "\n".join(lines)


def _runner_error(error: Error) -> Error:
    # command.run reports a missing program as code 127; the status line names the install instead.
    if isinstance(error, CommandFailed) and error.code == command.MISSING_PROGRAM_CODE:
        return NotFound("not_found", "mmdflux", _VERSION_FIX)
    return error


def status_text(snapshot: Snapshot) -> tuple[str, Error | None]:
    version = command.run(_VERSION_COMMAND, _VERSION_FIX)
    if isinstance(version, Err):
        return _fallback(snapshot), _runner_error(version.error)
    reported = version.value.stdout.strip()
    match = _VERSION_PATTERN.search(reported)
    actual = tuple(int(part) for part in match.groups()) if match else None
    if actual is None or actual < _MINIMUM_VERSION:
        return _fallback(snapshot), ConfigInvalid("config_invalid", "mmdflux_version", reported or "unknown", _VERSION_FIX)

    rendered = command.run(_COMMAND, _RENDER_FIX, stdin=snapshot.mermaid)
    if isinstance(rendered, Err):
        return _fallback(snapshot), _runner_error(rendered.error)
    return rendered.value.stdout, None
