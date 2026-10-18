from __future__ import annotations

from rich.text import Text

from .enums import CiState, MergeState, PrState
from .icons import icon, merge_icon
from .ids import short_id
from .render import _fit, render_agent
from .types import PrRow, PullRequest, Snapshot


def active_pr(pr: PullRequest) -> bool:
    return pr.state in (PrState.OPEN, PrState.DRAFT)


def _row(value: PrRow, snapshot: Snapshot) -> Text:
    line = Text("▲ " if value.top else "  ")
    glyph, style = icon(value.pr.state)
    line.append(f"{glyph} ", style=style)
    line.append(f"#{value.pr.number}", style=f"link {value.pr.url}")
    line.append(f"  {value.pr.head} → {value.pr.base}")
    line.append(f"  L{value.layer}" if value.layer is not None else "  L—")
    if value.bead_id:
        line.append(f"  {short_id(value.bead_id, snapshot.bead_prefix)}")
    if value.assignee:
        member = next((agent for agent in snapshot.agents if agent.name == value.assignee), None)
        line.append("  ")
        line.append_text(render_agent(value.assignee, member))
    if value.merge is None:
        line.append("  merge —")
    else:
        glyph, style = merge_icon(value.merge.state)
        files = f" {', '.join(value.merge.files)}" if value.merge.files and value.merge.state != MergeState.CLEAN else ""
        line.append(f"  merge {glyph}{files}", style=style)
    glyph, style = icon(value.pr.ci)
    checks = f" {', '.join(value.pr.failed_checks)}" if value.pr.failed_checks and value.pr.ci in (
        CiState.FAIL_MACOS, CiState.FAIL_WINDOWS, CiState.FAIL_LINUX, CiState.FAIL_BUG) else ""
    line.append(f"  CI {glyph}{checks}", style=style)
    return line


def render_prs(snapshot: Snapshot, width: int, include_history: bool = False) -> Text:
    rows = snapshot.prs if include_history else tuple(row for row in snapshot.prs if active_pr(row.pr))
    if not rows:
        if snapshot.gh.mode == "off":
            return Text("GitHub checks are off")
        if snapshot.gh.error is not None:
            return Text(f"GitHub read failed: {snapshot.gh.error.kind}")
        checked = snapshot.gh.at.strftime("%H:%M") if snapshot.gh.at else "unknown time"
        return Text(f"No open PR for phase {snapshot.phase} (checked {checked})")
    return Text("\n").join(_fit(_row(value, snapshot), width) for value in rows)
