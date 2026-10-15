from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text

from .ids import short_id
from .result import (
    CommandFailed,
    ConfigInvalid,
    Error,
    IoFailed,
    LockHeld,
    NotFound,
    ParseFailed,
    Rejected,
    Timeout,
    Unreachable,
)
from .types import Action, AgentView, Row, Snapshot

_STATUSES = {
    "busy": ("●", "green"),
    "stalling": ("◐", "yellow"),
    "blocked": ("■", "red"),
    "offline": ("○", "dim"),
    "idle": ("·", "dim"),
}


def _clock(value, *, seconds: bool = False) -> str:
    return value.astimezone().strftime("%H:%M:%S" if seconds else "%H:%M")


def _age(age_s: int) -> str:
    age_s = max(0, age_s)
    if age_s < 60:
        return f"{age_s}s"
    if age_s < 3600:
        return f"{age_s // 60}m"
    if age_s < 86400:
        return f"{age_s // 3600}h{(age_s % 3600) // 60:02d}"
    return f"{age_s // 86400}d"


def _fit(line: Text, width: int) -> Text:
    result = line.copy()
    result.truncate(max(0, width), overflow="ellipsis")
    return result


def _tag(row: Row) -> Text:
    result = Text()
    if row.marker == "red":
        result.append("❗", style="red")
    elif row.marker == "yellow":
        result.append("⚠️", style="yellow")
    result.append(row.tag)
    return result


def _icon(row: Row) -> str:
    if row.state == "running":
        return "🌀"
    if row.kind == "sprint_qa":
        return "🔍"
    if row.kind == "fix_qa":
        return "🛠️"
    if row.kind in ("dev", "fix"):
        return {"hard": "💎", "normal": "📥", "fast": "⚡"}.get(row.difficulty, "📥")
    return "📥"


def _agent_lines(agent: AgentView, prefix: str) -> list[Text]:
    glyph, color = _STATUSES[agent.status]
    heading = Text()
    heading.append(glyph, style=color)
    heading.append(" ")
    heading.append(agent.name, style="bold")
    heading.append(f"  {agent.depth}")
    red = sum(row.marker == "red" for row in agent.rows)
    yellow = sum(row.marker == "yellow" for row in agent.rows)
    if red:
        heading.append(f"  ❗{red}", style="red")
    if yellow:
        heading.append(f" ⚠️{yellow}", style="yellow")
    lines = [heading]
    rows = sorted(agent.rows, key=lambda row: row.state != "running")
    for row in rows[:3]:
        bead_id = short_id(row.task_id, prefix)
        line = Text(f" {_icon(row)} {bead_id}")
        line.append(" " * max(1, 22 - cell_len(bead_id)))
        line.append(f"{_age(row.age_s):>4} ")
        tag = _tag(row)
        if tag.plain:
            line.append_text(tag)
            line.append(": ")
        line.append(row.description)
        lines.append(line)
    if len(rows) > 3:
        lines.append(Text(f" … +{len(rows) - 3} more"))
    return lines


def _error_text(error: Error) -> str:
    if isinstance(error, CommandFailed):
        detail = f"{' '.join(error.argv)} (exit {error.code}): {error.stderr}"
    elif isinstance(error, Timeout):
        detail = f"{' '.join(error.argv)} timed out after {error.seconds}s"
    elif isinstance(error, Rejected):
        detail = f"{' '.join(error.argv)} (status {error.status}): {error.message}"
    elif isinstance(error, Unreachable):
        detail = f"{' '.join(error.argv)}: {error.message}"
    elif isinstance(error, ParseFailed):
        detail = f"{error.source}: {error.message}"
    elif isinstance(error, NotFound):
        detail = error.what
    elif isinstance(error, ConfigInvalid):
        detail = f"{error.key}={error.value}"
    elif isinstance(error, LockHeld):
        age = "unknown" if error.snapshot_age_s is None else f"{error.snapshot_age_s}s"
        detail = f"snapshot age {age}"
    elif isinstance(error, IoFailed):
        detail = f"{error.path}: {error.message}"
    return f"{error.kind}: {detail}; {error.fix}"


def _pr_link(line: Text, number: int, snapshot: Snapshot) -> None:
    match = next((row for row in snapshot.prs if row.pr.number == number), None)
    line.append(f" PR #{number}", style=f"link {match.pr.url}" if match else None)


def _sent_line(action: Action, snapshot: Snapshot) -> Text:
    beads = ", ".join(short_id(bead_id, snapshot.bead_prefix) for bead_id in action.bead_ids)
    line = Text(f" {_clock(action.at)} {beads} → {action.to}")
    matching = next((row for row in snapshot.prs if row.bead_id in action.bead_ids), None)
    if matching:
        _pr_link(line, matching.pr.number, snapshot)
    return line


def dashboard(snapshot: Snapshot, width: int, height: int) -> Text:
    lines = [Text(f"phase-{snapshot.phase} · {_clock(snapshot.generated_at)} · next {snapshot.interval_s}s")]
    if snapshot.stopped == "closed":
        lines.append(Text(f"stopped: phase closed {_clock(snapshot.generated_at)}"))
    elif snapshot.stopped == "idle":
        lines.append(Text("stopped: idle 60 min"))
    lines.append(Text())
    for agent in snapshot.agents:
        lines.extend(_agent_lines(agent, snapshot.bead_prefix))
    if snapshot.errors:
        lines.extend((Text(), Text(f"== Errors ({len(snapshot.errors)})")))
        # Error carries no time of its own, so each row shows the tick that surfaced it (generated_at), not the mock's per-error time.
        lines.extend(Text(f" {_clock(snapshot.generated_at)} {_error_text(error)}") for error in snapshot.errors)
    for section in snapshot.sections:
        if not section.rows:
            continue
        lines.extend((Text(), Text(f"== {section.label} ({len(section.rows)})")))
        for row in section.rows:
            line = Text(f"  {short_id(row.bead_id, snapshot.bead_prefix):<22} {_age(row.age_s):>4}  {row.reason}")
            if row.pr is not None:
                _pr_link(line, row.pr, snapshot)
            lines.append(line)
    if snapshot.sent:
        lines.extend((Text(), Text("== Sent")))
        lines.extend(_sent_line(action, snapshot) for action in snapshot.sent[-5:])
    if height <= 0:
        return Text()
    if len(lines) > height:
        omitted = len(lines) - height + 1
        lines = lines[:height - 1] + [Text(f"… {omitted} more rows")]
    return Text("\n").join(_fit(line, width) for line in lines)


def status_bar(snapshot: Snapshot, width: int) -> Text:
    gh = snapshot.gh
    if gh.mode == "off":
        segment = "gh: off"
    else:
        read_at = _clock(gh.at) if gh.at else "--:--"
        if gh.error is not None or gh.ok is False:
            kind = gh.error.kind if gh.error is not None else "unavailable"
            segment = f"gh: {gh.mode} ⛔ {kind} {read_at}"
        else:
            segment = f"gh: {gh.mode} ✅ {read_at}"
    if gh.truncated:
        segment += " 100+"
    metrics = snapshot.metrics
    filled = min(9, max(0, round(metrics.pct * 9 / 100)))
    progress = f"{metrics.done}/{metrics.total} [{'█' * filled}{'░' * (9 - filled)}] {metrics.pct}%"
    hours, minutes = divmod(max(0, metrics.active_s) // 60, 60)
    line = Text(f"{segment} | {progress} | active {hours}h{minutes:02d}m | tick {_clock(snapshot.generated_at, seconds=True)}")
    return _fit(line, width)
