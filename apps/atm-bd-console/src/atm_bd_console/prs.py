from __future__ import annotations

from rich.text import Text

from .ids import short_id
from .render import _fit
from .types import PrRow, Snapshot

_STATE_GLYPHS = {"open": "◉", "merged": "⑂", "closed": "⊗"}


def _row(value: PrRow, snapshot: Snapshot) -> Text:
    line = Text("▲ " if value.top else "  ")
    line.append(f"{_STATE_GLYPHS[value.pr.state]} ")
    line.append(f"#{value.pr.number}", style=f"link {value.pr.url}")
    line.append(f"  {value.pr.head} → {value.pr.base}")
    line.append(f"  L{value.layer}" if value.layer is not None else "  L—")
    if value.bead_id:
        line.append(f"  {short_id(value.bead_id, snapshot.bead_prefix)}")
    if value.merge is None:
        line.append("  merge —")
    elif value.merge.state == "clean":
        line.append("  merge ✅")
    else:
        files = f" {', '.join(value.merge.files)}" if value.merge.files else ""
        line.append(f"  merge ⛔{files}", style="red")
    if snapshot.gh.mode == "rest" or value.pr.ci == "none":
        line.append("  CI —")
    elif value.pr.ci == "success":
        line.append("  CI ✅", style="green")
    elif value.pr.ci == "pending":
        line.append("  CI 🌀", style="yellow")
    else:
        checks = f" {', '.join(value.pr.failed_checks)}" if value.pr.failed_checks else ""
        line.append(f"  CI ⛔{checks}", style="red")
    return line


def render_prs(snapshot: Snapshot, width: int, include_history: bool = False) -> Text:
    rows = snapshot.prs if include_history else tuple(row for row in snapshot.prs if row.pr.state == "open")
    if not rows:
        if snapshot.gh.mode == "off":
            return Text("GitHub checks are off")
        if snapshot.gh.error is not None:
            return Text(f"GitHub read failed: {snapshot.gh.error.kind}")
        checked = snapshot.gh.at.strftime("%H:%M") if snapshot.gh.at else "unknown time"
        return Text(f"No open PR for phase {snapshot.phase} (checked {checked})")
    return Text("\n").join(_fit(_row(value, snapshot), width) for value in rows)
