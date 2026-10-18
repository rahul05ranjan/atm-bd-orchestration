from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text

from .enums import SprintState
from .icons import (
    MARKER_FAST,
    MARKER_FIX_QA,
    MARKER_HARD,
    MARKER_QA,
    MARKER_RED,
    MARKER_YELLOW,
    SPRINT_ICONS,
    STATUS_FAIL,
    STATUS_OK,
    icon,
    icon_for,
)
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
from .types import Action, AgentView, Member, Row, Snapshot

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


def tick_error(error: Error, width: int) -> tuple[Text, Text]:
    """Render a tick error as single-line, width-fitted dashboard and header text."""
    message = " ".join(getattr(error, "message", error.kind).split())
    dashboard = Text("\n").join((
        _fit(Text("== Errors (1)", style="red"), width),
        _fit(Text(f" {error.kind}: {message}", style="red"), width),
    ))
    header = _fit(Text(f"{error.kind}: {error.fix}", style="red"), width)
    return dashboard, header


def _tag(row: Row) -> Text:
    result = Text()
    if row.marker == "red":
        result.append(MARKER_RED, style="red")
    elif row.marker == "yellow":
        result.append(MARKER_YELLOW, style="yellow")
    result.append(row.tag)
    return result


def render_agent(name: str | None, member: Member | AgentView | None) -> Text:
    """Render one agent name with its roster icon immediately before the name."""
    if not name:
        return Text("unassigned")
    glyph, style = icon_for(member)
    result = Text()
    result.append(glyph, style=style)
    result.append(name, style="bold")
    return result


def _task_marker(row: Row) -> str | None:
    if row.kind == "sprint_qa":
        return MARKER_QA
    if row.kind == "fix_qa":
        return MARKER_FIX_QA
    if row.kind in ("dev", "fix"):
        return {"hard": MARKER_HARD, "fast": MARKER_FAST}.get(row.difficulty or "")
    return None


def _icon(row: Row) -> Text:
    status, style = SPRINT_ICONS[SprintState.IN_PROGRESS if row.state == "running" else SprintState.READY]
    result = Text(status, style=style or "")
    marker = _task_marker(row)
    if marker:
        result.append(marker)
    return result


def _agent_lines(agent: AgentView, prefix: str) -> list[Text]:
    glyph, color = _STATUSES[agent.status]
    heading = Text()
    heading.append(glyph, style=color)
    heading.append(" ")
    heading.append_text(render_agent(agent.name, agent))
    heading.append(f"  {agent.depth}")
    red = sum(row.marker == "red" for row in agent.rows)
    yellow = sum(row.marker == "yellow" for row in agent.rows)
    if red:
        heading.append(f"  {MARKER_RED}{red}", style="red")
    if yellow:
        heading.append(f" {MARKER_YELLOW}{yellow}", style="yellow")
    lines = [heading]
    rows = sorted(agent.rows, key=lambda row: row.state != "running")
    for row in rows[:3]:
        bead_id = short_id(row.task_id, prefix)
        line = Text(" ")
        line.append_text(_icon(row))
        line.append(f" {bead_id}")
        line.append(" " * max(1, 22 - cell_len(bead_id)))
        line.append(f"{_age(row.age_s):>4} ")
        tag = _tag(row)
        if tag.plain:
            line.append_text(tag)
            line.append(": ")
        line.append(" ".join(row.description.split()))
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
    else:
        # A new Error variant must get its own branch here; never fall through to an unset detail.
        raise TypeError(f"unknown error variant {type(error).__name__}; add it to _error_text")
    return f"{error.kind}: {detail}; {error.fix}"


def _pr_link(line: Text, number: int, snapshot: Snapshot) -> None:
    match = next((row for row in snapshot.prs if row.pr.number == number), None)
    line.append(f" PR #{number}", style=f"link {match.pr.url}" if match else None)


def _sent_line(action: Action, snapshot: Snapshot) -> Text:
    beads = ", ".join(short_id(bead_id, snapshot.bead_prefix) for bead_id in action.bead_ids)
    line = Text(f" {_clock(action.at)} {beads} → ")
    recipient = next((agent for agent in snapshot.agents if agent.name == action.to), None)
    line.append_text(render_agent(action.to, recipient))
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
    if snapshot.stopped is not None and snapshot.sprints:
        beads = tuple(step for sprint in snapshot.sprints for step in sprint.steps)
        lines.extend((Text(), Text(f"== Beads ({len(beads)})")))
        for step in beads:
            bead_line = Text(f" {short_id(step.bead_id, snapshot.bead_prefix):<22} ")
            status_glyph, status_style = icon(step.status)
            bead_line.append(status_glyph, style=status_style or "")
            bead_line.append(" ")
            bead_line.append(f"{step.status:<12} {step.kind}")
            lines.append(bead_line)
    if snapshot.stopped is not None and snapshot.prs:
        lines.extend((Text(), Text(f"== PRs ({len(snapshot.prs)})")))
        for row in snapshot.prs:
            line = Text(f" PR #{row.pr.number} {row.pr.head} · {row.pr.ci}")
            line.stylize(f"link {row.pr.url}", 1, 1 + len(f"PR #{row.pr.number}"))
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
        if gh.paused:
            resource = "graphql" if gh.mode == "graphql" else "core"
            reset = _clock(gh.quota_reset) if gh.quota_reset else "unknown"
            reason = "rate limited" if gh.rate_limited else f"below {gh.quota_threshold} percent"
            segment = f"GitHub paused: {gh.quota_remaining}/{gh.quota_limit} {resource} remaining, {reason}, resets {reset}"
            if width < 100:
                short_reason = "rate limited" if gh.rate_limited else f"below {gh.quota_threshold}%"
                segment = f"GitHub paused: {gh.quota_remaining}/{gh.quota_limit} {resource}, {short_reason}, reset {reset}"
        elif gh.error is not None or gh.ok is False:
            kind = gh.error.kind if gh.error is not None else "unavailable"
            segment = f"gh: {gh.mode} {STATUS_FAIL} {kind} {read_at}"
        else:
            segment = f"gh: {gh.mode} {STATUS_OK} {read_at}"
    if gh.truncated:
        segment += " 100+"
    segment += f" | gh poll {gh.poll_interval_s}s / {gh.quota_threshold}%"
    metrics = snapshot.metrics
    filled = min(9, max(0, round(metrics.pct * 9 / 100)))
    progress = f"{metrics.done}/{metrics.total} [{'█' * filled}{'░' * (9 - filled)}] {metrics.pct}%"
    hours, minutes = divmod(max(0, metrics.active_s) // 60, 60)
    line = Text(f"{segment} | {progress} | active {hours}h{minutes:02d}m | tick {_clock(snapshot.generated_at, seconds=True)}")
    if snapshot.lead:
        agent = next((item for item in snapshot.agents if item.name == snapshot.lead), None)
        line.append(" | lead ")
        line.append_text(render_agent(snapshot.lead, agent))
    return _fit(line, width)
