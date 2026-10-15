from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from rich.cells import cell_len

from atm_bd_console.codec import from_json
from atm_bd_console.prs import render_prs
from atm_bd_console.result import Ok
from atm_bd_console.types import GhStatus, Merge, PrRow, PullRequest, Snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "contract"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    lower = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", "failure", ("lint", "test"))
    top = PullRequest(12, "https://example/pr/12", "branch-2", "branch-1", "sha2", "success", ())
    rows = (
        PrRow(lower, 1, "atm-bh-1.group-dev", False,
              Merge("branch-1", "base", "conflict", ("model.py",), (), NOW)),
        PrRow(top, 2, "atm-bh-2.group-dev", True,
              Merge("branch-2", "branch-1", "clean", (), (), NOW)),
    )
    return replace(decoded.value, prs=rows, gh=GhStatus("graphql", True, None, NOW, False))


def test_lower_red_and_green_top_are_display_only() -> None:
    rendered = render_prs(snapshot(), 120)
    lines = rendered.plain.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("  #11") and "CI ⛔ lint, test" in lines[0]
    assert "merge ⛔ model.py" in lines[0]
    assert lines[1].startswith("▲ #12") and "CI ✅" in lines[1] and "merge ✅" in lines[1]
    assert "L1" in lines[0] and "bh-1.group-dev" in lines[0]
    assert "L2" in lines[1] and "bh-2.group-dev" in lines[1]
    assert any("link https://example/pr/11" in str(span.style) for span in rendered.spans)


def test_rest_mode_and_width() -> None:
    value = replace(snapshot(), gh=GhStatus("rest", True, None, NOW, False))
    assert all("CI —" in line for line in render_prs(value, 120).plain.splitlines())
    for width in (80, 120):
        assert all(cell_len(line) <= width for line in render_prs(snapshot(), width).plain.splitlines())
