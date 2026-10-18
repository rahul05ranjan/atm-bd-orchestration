from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.cells import cell_len

from atm_bd_console.codec import from_json
from atm_bd_console.icons import CI_STATE_ICONS, PR_STATE_ICONS, ci_failure_key
from atm_bd_console.prs import render_prs
from atm_bd_console.result import Ok, Timeout
from atm_bd_console.types import AgentView, GhStatus, Merge, PrRow, PullRequest, Snapshot

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
    assert lines[0].startswith("  \U000f04c2 #11") and "CI \U000f00e4 lint, test" in lines[0]
    assert "merge \U000f062c model.py" in lines[0]
    assert lines[1].startswith("▲ \U000f04c2 #12") and "CI \U000f062c" in lines[1] and "merge \U000f062d" in lines[1]
    assert "L1" in lines[0] and "bh-1.group-dev" in lines[0]
    assert "L2" in lines[1] and "bh-2.group-dev" in lines[1]
    assert any("link https://example/pr/11" in str(span.style) for span in rendered.spans)


def test_merge_error_uses_the_failure_glyph_without_a_file_list() -> None:
    # The merge error keeps the GH failure glyph and does not name files when none were reported.
    lower = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", "success", ())
    failed = PrRow(lower, 1, "atm-bh-1.group-dev", False,
                   Merge("branch-1", "base", "error", (), ("merge-tree failed",), NOW))
    line = render_prs(replace(snapshot(), prs=(failed,)), 120).plain
    assert "merge \U000f062d  CI \U000f062c" in line


@pytest.mark.parametrize(
    ("failed_checks", "glyph"),
    [
        (("build (macos-14)",), "\U000f0035"),
        (("test (windows-latest)",), "\U000f05b3"),
        (("ubuntu-22.04 pytest",), "\U000f033d"),
        (("build (macos-14)", "lint"), "\U000f00e4"),
    ],
    ids=["macos", "windows", "linux", "platform-plus-general"],
)
def test_failing_pr_renders_the_platform_or_bug_glyph_in_red(failed_checks: tuple[str, ...], glyph: str) -> None:
    base = snapshot()
    red = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", "failure", failed_checks)
    row = replace(base.prs[0], pr=red, merge=None)
    rendered = render_prs(replace(base, prs=(row,)), 120)
    assert f"CI {glyph} " in rendered.plain
    segments = tuple((rendered.plain[span.start:span.end], str(span.style)) for span in rendered.spans)
    assert any(f"CI {glyph}" in segment and style == "#F85149" for segment, style in segments)


def test_rest_mode_and_width() -> None:
    value = replace(snapshot(), gh=GhStatus("rest", True, None, NOW, False))
    assert all("CI \U000f01d8" in line for line in render_prs(value, 120).plain.splitlines())
    for width in (80, 120):
        assert all(cell_len(line) <= width for line in render_prs(snapshot(), width).plain.splitlines())


def test_pr_assignee_uses_roster_icon_before_name() -> None:
    agent = AgentView("codex-sol", "busy", NOW, 0, (), "dev", "GPT-5.6-Sol", "codex-cli")
    row = replace(snapshot().prs[1], assignee=agent.name)
    rendered = render_prs(replace(snapshot(), prs=(row,), agents=(agent,)), 120).plain
    assert "\U000f05a8codex-sol" in rendered


@pytest.mark.parametrize("phase", ("bh", "phase-current"))
@pytest.mark.parametrize("mode", ("graphql", "rest"))
def test_successful_empty_prs_name_actual_phase_and_checked_time(phase: str, mode: str) -> None:
    empty = replace(snapshot(), phase=phase, prs=(), gh=GhStatus(mode, True, None, NOW, False))
    assert render_prs(empty, 120).plain == f"No open PR for phase {phase} (checked 12:00)"


def test_empty_prs_distinguish_github_off_and_failure() -> None:
    empty = replace(snapshot(), prs=())
    assert render_prs(replace(empty, gh=GhStatus("off", None, None, None, False)), 120).plain == "GitHub checks are off"
    failed = GhStatus("graphql", False, Timeout("timeout", ("gh",), 5, "retry"), NOW, False)
    assert render_prs(replace(empty, gh=failed), 120).plain == "GitHub read failed: timeout"


def test_history_filter_and_glyphs_cover_all_pull_request_states() -> None:
    base = snapshot()
    merged = replace(base.prs[0], pr=replace(base.prs[0].pr, state="merged"))
    closed = replace(base.prs[1], pr=replace(base.prs[1].pr, state="closed"))
    draft = replace(base.prs[1], pr=replace(base.prs[1].pr, state="draft"))
    value = replace(base, prs=(base.prs[0], merged, closed, draft))
    assert render_prs(value, 120).plain.count("#11") == 1
    rendered = render_prs(value, 120, include_history=True)
    assert "\U000f04c2 #11" in rendered.plain and "\U000f062d #11" in rendered.plain
    assert "\U000f073a #12" in rendered.plain and "\U000f0cb6 #12" in rendered.plain
    glyph_styles = {rendered.plain[span.start:span.end]: str(span.style) for span in rendered.spans}
    assert glyph_styles["\U000f04c2 "] == "#2D81E0" and glyph_styles["\U000f062d "] == "#8957E5"
    assert glyph_styles["\U000f073a "] == "#F85149" and glyph_styles["\U000f0cb6 "] == "#FFA60D"


def test_pr_list_pins_passing_failing_running_merged_and_closed_rows() -> None:
    base = snapshot()
    cases = (
        ("open", "success"),
        ("open", "failure"),
        ("open", "pending"),
        ("merged", "success"),
        ("closed", "success"),
    )
    rows = tuple(
        replace(
            base.prs[0],
            pr=replace(base.prs[0].pr, number=20 + index, state=state, ci=ci),
            top=False,
            merge=None,
        )
        for index, (state, ci) in enumerate(cases)
    )
    lines = render_prs(replace(base, prs=rows), 120, include_history=True).split("\n")

    assert len(lines) == len(cases)
    for line, (state, ci) in zip(lines, cases, strict=True):
        pr_glyph, pr_color = PR_STATE_ICONS[state]
        ci_key = ci_failure_key(base.prs[0].pr.failed_checks) if ci == "failure" else ci
        ci_glyph, ci_color = CI_STATE_ICONS[ci_key]
        assert f"{pr_glyph} " in line.plain
        assert f"CI {ci_glyph}" in line.plain
        segments = tuple((line.plain[span.start:span.end], str(span.style)) for span in line.spans)
        assert (f"{pr_glyph} ", pr_color) in segments
        assert any(f"CI {ci_glyph}" in segment and style == ci_color for segment, style in segments)
