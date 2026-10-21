from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.cells import cell_len

from atm_bd_console.ci import ci_failure_key
from atm_bd_console.codec import from_json
from atm_bd_console.enums import CiState, MergeState, PrState
from atm_bd_console.error_text import error_text
from atm_bd_console.icons import icon
from atm_bd_console.prs import render_prs
from atm_bd_console.result import Ok, Timeout
from atm_bd_console.types import AgentView, GhStatus, Merge, PrRow, PullRequest, Snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "contract"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    lower = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", CiState.FAIL_BUG, ("lint", "test"))
    top = PullRequest(12, "https://example/pr/12", "branch-2", "branch-1", "sha2", CiState.PASS, ())
    rows = (
        PrRow(lower, 1, "atm-bh-1.group-dev", False,
              Merge("branch-1", "base", MergeState.CONFLICT, ("model.py",), (), NOW)),
        PrRow(top, 2, "atm-bh-2.group-dev", True,
              Merge("branch-2", "branch-1", MergeState.CLEAN, (), (), NOW)),
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
    lower = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", CiState.PASS, ())
    failed = PrRow(lower, 1, "atm-bh-1.group-dev", False,
                   Merge("branch-1", "base", MergeState.ERROR, (), ("merge-tree failed",), NOW))
    line = render_prs(replace(snapshot(), prs=(failed,)), 120).plain
    assert "merge \U000f062d merge-tree failed  CI \U000f062c" in line  # the error's own message, never just an icon


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
    red = PullRequest(11, "https://example/pr/11", "branch-1", "base", "sha1", ci_failure_key(failed_checks), failed_checks)
    row = replace(base.prs[0], pr=red, merge=None)
    rendered = render_prs(replace(base, prs=(row,)), 120)
    assert f"CI {glyph} " in rendered.plain
    segments = tuple((rendered.plain[span.start:span.end], str(span.style)) for span in rendered.spans)
    assert any(f"CI {glyph}" in segment and style == "#F85149" for segment, style in segments)


def test_rest_mode_and_width() -> None:
    value = replace(snapshot(), gh=GhStatus("rest", True, None, NOW, False))
    lines = render_prs(value, 120).plain.splitlines()
    assert len(lines) == 2
    assert "CI \U000f00e4" in lines[0] and "CI \U000f062c" in lines[1]
    for width in (80, 120):
        assert all(cell_len(line) <= width for line in render_prs(snapshot(), width).plain.splitlines())


def test_pr_assignee_uses_roster_icon_before_name() -> None:
    agent = AgentView("codex-sol", "busy", NOW, 0, (), "dev", "GPT-5.6-Sol", "codex-cli")
    row = replace(snapshot().prs[1], assignee=agent.name)
    rendered = render_prs(replace(snapshot(), prs=(row,), agents=(agent,)), 120).plain
    assert "\U000f05a8codex-sol" in rendered


@pytest.mark.parametrize("phase", ("bh", "phase-current"))
@pytest.mark.parametrize("mode", ("graphql", "rest"))
def test_successful_empty_prs_name_actual_phase_and_checked_time(phase: str, mode: str, monkeypatch) -> None:
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Etc/GMT+8")
    time.tzset()
    try:
        empty = replace(snapshot(), phase=phase, prs=(), gh=GhStatus(mode, True, None, NOW, False))
        assert render_prs(empty, 120).plain == f"No open PR for phase {phase} (checked 04:00)"
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        time.tzset()


def test_empty_prs_distinguish_github_off_and_failure() -> None:
    empty = replace(snapshot(), prs=())
    assert render_prs(replace(empty, gh=GhStatus("off", None, None, None, False)), 120).plain == "GitHub checks are off"
    failed = GhStatus("graphql", False, Timeout("timeout", ("gh",), 5, "retry"), NOW, False)
    assert render_prs(replace(empty, gh=failed), 120).plain == f"GitHub read failed: {error_text(failed.error)}"
    never = GhStatus("graphql", None, None, None, False)
    assert render_prs(replace(empty, gh=never), 120).plain == "PRs not yet read"  # no data is not "no open PR"


def test_cached_rows_after_a_failed_read_are_marked_stale_with_the_read_time_and_typed_error() -> None:
    value = snapshot()
    failed = GhStatus("graphql", False, Timeout("timeout", ("gh", "api"), 5, "retry"), NOW, False)
    read_at = NOW - timedelta(minutes=7)
    lines = render_prs(replace(value, gh=failed), 200, read_at=read_at).plain.splitlines()
    assert lines[0] == f"stale since {read_at.astimezone().strftime('%H:%M')}: {error_text(failed.error)}"
    assert "#11" in lines[1]
    assert "stale" not in render_prs(value, 200).plain
    unknown = render_prs(replace(value, gh=failed), 200).plain.splitlines()[0]
    assert unknown.startswith("stale (read time unknown): ")


@pytest.mark.parametrize("cached", ("rows", "empty", "none"))
def test_paused_notice_precedes_cached_rows_and_empty_states(cached: str) -> None:
    base = snapshot()
    gh = replace(base.gh, paused=True, quota_remaining=2450, quota_limit=5000,
                 quota_reset=NOW, at=None if cached == "none" else NOW,
                 ok=None if cached == "none" else True)
    value = replace(base, gh=gh, prs=base.prs if cached == "rows" else ())
    lines = render_prs(value, 120).plain.splitlines()
    assert lines[0] == (f"GitHub paused: 2450/5000 graphql remaining, below 50 percent, "
                        f"resets {NOW.astimezone().strftime('%H:%M')}")
    if cached == "rows":
        assert "#11" in lines[1] and "#12" in lines[2]
    elif cached == "empty":
        assert lines[1].startswith("No open PR for phase")
    else:
        assert len(lines) == 1


def test_history_filter_and_glyphs_cover_all_pull_request_states() -> None:
    base = snapshot()
    merged = replace(base.prs[0], pr=replace(base.prs[0].pr, state=PrState.MERGED))
    closed = replace(base.prs[1], pr=replace(base.prs[1].pr, state=PrState.CLOSED))
    draft = replace(base.prs[1], pr=replace(base.prs[1].pr, state=PrState.DRAFT))
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
        (PrState.OPEN, CiState.PASS),
        (PrState.OPEN, CiState.FAIL_BUG),
        (PrState.OPEN, CiState.RUNNING),
        (PrState.MERGED, CiState.PASS),
        (PrState.CLOSED, CiState.PASS),
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
        pr_glyph, pr_color = icon(state)
        ci_glyph, ci_color = icon(ci)
        assert f"{pr_glyph} " in line.plain
        assert f"CI {ci_glyph}" in line.plain
        segments = tuple((line.plain[span.start:span.end], str(span.style)) for span in line.spans)
        assert (f"{pr_glyph} ", pr_color) in segments
        assert any(f"CI {ci_glyph}" in segment and style == ci_color for segment, style in segments)
