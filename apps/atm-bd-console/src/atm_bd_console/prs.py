from __future__ import annotations

from rich.text import Text

from .types import PrRow, Snapshot


def _row(value: PrRow, snapshot: Snapshot) -> Text:
    line = Text("▲ " if value.top else "  ")
    line.append(f"#{value.pr.number}", style=f"link {value.pr.url}")
    line.append(f"  {value.pr.head} → {value.pr.base}")
    line.append(f"  L{value.layer}" if value.layer is not None else "  L—")
    if value.bead_id:
        line.append(f"  {value.bead_id.removeprefix(f'{snapshot.bead_prefix}-')}")
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


def render_prs(snapshot: Snapshot, width: int) -> Text:
    lines: list[Text] = []
    for value in snapshot.prs:
        line = _row(value, snapshot)
        line.truncate(max(0, width), overflow="ellipsis")
        lines.append(line)
    return Text("\n").join(lines)
