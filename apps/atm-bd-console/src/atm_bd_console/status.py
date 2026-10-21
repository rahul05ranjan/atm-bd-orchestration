from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from . import command
from .enums import SprintState
from .error_text import error_text
from .icons import SPRINT_ICONS
from .ids import short_id
from .render import render_agent
from .result import CommandFailed, ConfigInvalid, Err, Error, NotFound, Result, Timeout
from .types import Snapshot, SprintView

_COMMAND = ("mmdflux", "--format", "text")
_VERSION_COMMAND = ("mmdflux", "--version")
_MINIMUM_VERSION = (2, 6, 1)
_VERSION_PATTERN = re.compile(r"\b([0-9]+)\.([0-9]+)\.([0-9]+)\b")
_VERSION_FIX = "install or upgrade mmdflux >=2.6.1"
_MERMAID_LINES = 20
_NO_PHASE_FIX = "check the bd reads in the console log; the diagram returns when a read succeeds"


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


def _render_fix(error: Error, mermaid: str) -> Error:
    """The fix for THIS failure of the render: a parse error is a defect to report, a timeout is a loaded host."""
    if isinstance(error, CommandFailed):
        head = "; ".join(mermaid.splitlines()[:_MERMAID_LINES])
        return replace(error, fix=f"mmdflux rejected the generated Mermaid: report it as a defect with the first {_MERMAID_LINES} lines: {head}")
    if isinstance(error, Timeout):
        return replace(error, fix=f"the host is loaded: mmdflux was waited for {error.seconds}s; it is retried next tick")
    return error


Runner = Callable[..., Result[command.Output]]


def status_text(snapshot: Snapshot, run: Runner = command.run) -> tuple[str, Error | None]:
    """Without sprints there is no diagram to draw: mmdflux is not run and the error is the upstream read error."""
    if not snapshot.sprints:
        return "", snapshot.errors[0] if snapshot.errors else NotFound("not_found", "phase data", _NO_PHASE_FIX)
    version = run(_VERSION_COMMAND, _VERSION_FIX)
    if isinstance(version, Err):
        return _fallback(snapshot), _runner_error(version.error)
    reported = version.value.stdout.strip()
    match = _VERSION_PATTERN.search(reported)
    actual = tuple(int(part) for part in match.groups()) if match else None
    if actual is None or actual < _MINIMUM_VERSION:
        return _fallback(snapshot), ConfigInvalid("config_invalid", "mmdflux_version", reported or "unknown", _VERSION_FIX)

    rendered = run(_COMMAND, "", stdin=snapshot.mermaid)
    if isinstance(rendered, Err):
        return _fallback(snapshot), _runner_error(_render_fix(rendered.error, snapshot.mermaid))
    return rendered.value.stdout, None


def _age(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m"
    return f"{whole // 3600}h"


def _short_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """A `bd` call over many ids prints as `bd show (28 ids)` in the pane; the full argv stays in the log and Errors."""
    ids = [part for part in argv[2:] if not part.startswith("-")]
    return (*argv[:2], f"({len(ids)} ids)") if len(ids) > 1 else argv


def _reason(error: Error) -> str:
    argv = getattr(error, "argv", None)
    if argv and argv[0] == "bd" and _short_argv(tuple(argv)) != tuple(argv):
        return error_text(replace(error, argv=_short_argv(tuple(argv))))  # type: ignore[type-var]
    return error_text(error)


@dataclass
class DagPane:
    """The DAG pane's memory: the last diagram that rendered and when. A failed render never replaces it."""

    log: Callable[[Error], None] | None = None
    last_good: tuple[str, datetime] | None = None

    def show(self, snapshot: Snapshot, now: datetime, render: Callable[[Snapshot], tuple[str, Error | None]] | None = None) -> tuple[str, Error | None]:
        text, error = (render or status_text)(snapshot)
        if error is None:
            self.last_good = (text, now)
            return text, None
        if self.log is not None and error not in snapshot.errors:
            self.log(error)  # upstream read errors were logged where they happened; this one is the render's own
        if self.last_good is not None:
            diagram, since = self.last_good
            return f"{diagram}\n\nstale {_age((now - since).total_seconds())}  {_reason(error)}", error
        return f"no diagram yet, text fallback below\n{_reason(error)}\n\n{text}".rstrip("\n"), error
