from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from contract_samples import STEP
from rich.cells import cell_len

from atm_bd_console import atm_source
from atm_bd_console.codec import from_json
from atm_bd_console.enums import CiState, SprintState
from atm_bd_console.model import _agent_views
from atm_bd_console.render import _age, _error_text, dashboard, github_quotas, status_bar
from atm_bd_console.result import (
    CommandFailed,
    ConfigInvalid,
    IoFailed,
    LockHeld,
    NotFound,
    Ok,
    ParseFailed,
    Rejected,
    Timeout,
    Unreachable,
)
from atm_bd_console.types import (
    Action,
    AgentView,
    GhStatus,
    Member,
    Metrics,
    Phase,
    PrRow,
    PullRequest,
    Row,
    Section,
    SectionRow,
    Snapshot,
    Step,
    Task,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 14, 21, 4, 31, tzinfo=UTC)


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    return replace(decoded.value, generated_at=NOW)


def pacific_test(operation) -> None:
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        operation()
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def row(task_id: str, state: str, kind: str, difficulty: str | None, age_s: int,
        marker: str | None = None) -> Row:
    return Row(task_id, state, kind, difficulty, "bh", marker, age_s, f"description for {task_id}")


def test_dashboard_agent_rows_styles_icons_and_overflow() -> None:
    agents = (
        AgentView("busy", "busy", NOW, 4, (
            row("atm-running", "running", "dev", "normal", 45),
            row("atm-hard", "queued", "dev", "hard", 32 * 60, "red"),
            row("atm-normal", "queued", "fix", "normal", 64 * 60, "yellow"),
            row("atm-fast", "queued", "fix", "fast", 2 * 86400),
        )),
        AgentView("stalling", "stalling", NOW, 1, (row("atm-sprint-qa", "queued", "sprint_qa", None, 12),)),
        AgentView("blocked", "blocked", NOW, 1, (row("atm-fix-qa", "queued", "fix_qa", None, 13),)),
        AgentView("offline", "offline", NOW, 1, (row("atm-other", "queued", "other", None, 14),)),
        AgentView("idle", "idle", NOW, 0, ()),
    )
    value = replace(snapshot(), agents=agents)

    def check() -> None:
        rendered = dashboard(value, 120, 40)
        text = rendered.plain
        assert text.startswith("phase-bh · 14:04 · next 60s")
        assert "● ·busy  4  ❗1 ⚠️1" in text
        assert "\U000f051f running" in text and "💎 hard" in text and "\U000f040d normal" in text
        assert "… +1 more" in text and "⚡ fast" not in text
        assert "◐ ·stalling" in text and "🔍 sprint-qa" in text
        assert "■ ·blocked" in text and "🛠️ fix-qa" in text
        assert "○ ·offline" in text and "· ·idle" in text
        assert "❗bh: description for atm-hard" in text
        assert "⚠️bh: description for atm-normal" in text
        styles = {span.style for span in rendered.spans}
        assert {"green", "yellow", "red", "dim", "bold"} <= styles

    pacific_test(check)


def test_dashboard_renders_every_member_from_roster_fixture(fake_bin, monkeypatch) -> None:
    monkeypatch.setenv("ATM_BODY", (FIXTURES / "atm" / "members.json").read_text())
    fake_bin.make("atm", 'printf "%s" "$ATM_BODY"\n')
    members = atm_source.members("atm-dev", "team-lead")
    assert isinstance(members, Ok)
    agents = _agent_views(members.value, (), None, Ok(()), NOW, "atm")
    rendered = dashboard(replace(snapshot(), agents=agents, errors=(), sections=(), sent=()), 120, 40)

    for name, glyph, style in (
        ("aria", "\U000f05a8", "#FFA60D"),
        ("boris", "\U000f06c4", "#FF8C66"),
        ("cy", "\U000f01a6", "#E551BA"),
        ("dara", "\U000f029b", None),
        ("eli", "\U000f01e7", "#2DE057"),
    ):
        position = rendered.plain.index(glyph + name)
        if style is not None:
            assert any(span.start == position and span.end == position + 1 and span.style == style
                       for span in rendered.spans)


def test_sections_error_first_sent_last_height_and_links() -> None:
    error = Timeout("timeout", ("atm",), 5, "retry")
    pr = PullRequest(553, "https://example/pr/553", "head", "base", "sha", CiState.RUNNING, ())
    actions = tuple(Action(NOW - timedelta(minutes=number), "send", "dev-sanity", ("atm-bh-5",), "sent", None)
                    for number in range(6))
    value = replace(
        snapshot(), errors=(error,), sections=(Section("Held", (SectionRow("atm-bh-6", 180, "conflict", 553),)),),
        sent=actions, prs=(PrRow(pr, 5, "atm-bh-5", True, None),),
    )

    def check() -> None:
        rendered = dashboard(value, 120, 40)
        text = rendered.plain
        assert text.index("== Errors (1)") < text.index("== Held (1)") < text.index("== Sent")
        assert "14:04 timeout:" in text and "retry" in text
        assert "bh-6" in text and "3m" in text and "PR #553" in text
        assert text.count("→ ·dev-sanity") == 5
        assert any("link https://example/pr/553" in str(span.style) for span in rendered.spans)
        clipped = dashboard(value, 80, 10).plain.splitlines()
        assert len(clipped) == 10 and clipped[-1].startswith("… ") and "more rows" in clipped[-1]
        for width in (80, 120):
            for line in dashboard(value, width, 40).plain.splitlines():
                assert cell_len(line) <= width

    pacific_test(check)


def test_long_titles_with_wide_chars_and_emoji_clip_at_80_and_120() -> None:
    long_row = Row("atm-long-title", "running", "dev", "hard", "bh", "red", 45,
                   "🌀 界界界界界界界界界界界界界界界界界界界界界界界界 emoji 💎📥⚠️ " + "x" * 120)
    value = replace(snapshot(), agents=(AgentView("busy", "busy", NOW, 1, (long_row,)),), errors=(), sections=())

    for width in (80, 120):
        lines = dashboard(value, width, 40).plain.splitlines()
        assert all(cell_len(line) <= width for line in lines), width
        assert any(line.endswith("…") for line in lines), width
        assert any("\U000f051f💎 long-title" in line and "❗bh:" in line for line in lines), width
        row = next(line for line in lines if "long-title" in line)
        assert row.startswith(" \U000f051f💎 long-title"), width

    assert any("💎" in line for line in dashboard(value, 120, 40).plain.splitlines())


@pytest.mark.parametrize("width", (80, 120, 240))
def test_multiline_task_description_stays_on_one_width_fitted_row(width: int) -> None:
    description = "\n<review>\n  Inspect\t界 branch\r\n  and  report findings.\n</review>\n" + "detail " * 20
    task_row = replace(row("atm-review", "running", "sprint_qa", None, 45), description=description)
    value = replace(snapshot(), agents=(AgentView("reviewer", "busy", NOW, 1, (task_row,)),),
                    errors=(), sections=(), sent=())

    lines = dashboard(value, width, 40).plain.splitlines()

    assert len(lines) == 4
    assert all(cell_len(line) <= width for line in lines)
    assert "<review> Inspect 界 branch" in lines[3]
    assert "\t" not in lines[3]
    if width < 240:
        assert lines[3].endswith("…")
    else:
        assert lines[3].endswith("<review> Inspect 界 branch and report findings. </review> " + "detail " * 19 + "detail")


@pytest.mark.parametrize("known_step", [True, False])
@pytest.mark.parametrize(("description", "summary"), [
    (('<atm-task id="atm-bi-6.group-dev" sprint="bi-6"> <description><![CDATA['
      'Implement dev bead `atm-bi-6.group-dev`…'), 'Implement dev bead `atm-bi-6.group-dev`…'),
    (('<atm-task id="atm-bi-6.group-dev" sprint="bi-6">\n<description><![CDATA[\n'
      '  Implement\tdev bead\nLater instructions\n]]></description>\n</atm-task>'), 'Implement dev bead'),
    ('\n  Plain\ttext summary\nLater instructions', 'Plain text summary'),
    (' \n\t', ''),
])
def test_dispatch_task_uses_bead_title_or_plain_first_line(known_step: bool, description: str, summary: str) -> None:
    task_id = "atm-bi-6.group-dev"
    title = "Implement status table"
    task = Task(task_id, "arch-ctm", "active", 0, NOW, NOW, description)
    step = replace(STEP, bead_id=task_id, kind="dev", title=title, top_epic="atm-phase-bi", tag="phase-bi")
    phase = Phase("atm-phase-bi", "phase-bi", "Phase BI", False, (), (step,) if known_step else (), ())
    agents = _agent_views((Member("arch-ctm", "active", NOW),), (task,), phase, Ok(()), NOW, "atm")
    expected = title if known_step else summary
    assert agents[0].rows[0].description == expected
    value = replace(snapshot(), agents=agents, errors=(), sections=(), sent=())

    line = next(line for line in dashboard(value, 240, 40).plain.splitlines() if "bi-6.group-dev" in line)

    assert "<" not in line and "CDATA" not in line and "]]>" not in line
    assert "Later instructions" not in line
    assert expected in line
    if expected:
        assert line.index("bi-6.group-dev") < line.index(expected)


def test_status_bar_formats_and_cell_width() -> None:
    value = replace(snapshot(), metrics=Metrics(3, 9, 33, 14 * 3600 + 20 * 60),
                    gh=GhStatus("graphql", True, None, NOW, False))

    def check() -> None:
        expected = (FIXTURES / "render" / "status-bar-ok.txt").read_text().rstrip("\n")
        assert status_bar(value, 200).plain == "gh core ?/? · graphql ?/? · reset --:-- | " + expected.replace(" | ", " | gh poll 600s / 50% | ", 1) + " | lead ·fenix"
        assert status_bar(replace(value, gh=GhStatus("off", None, None, None, False)), 120).plain == (
            "gh: off | gh poll 600s / 50% | 3/9 [███░░░░░░] 33% | active 14h20m | tick 14:04:31 | lead ·fenix"
        )
        failed = replace(value, gh=GhStatus("graphql", False, Timeout("timeout", ("gh",), 5, "retry"), NOW, True))
        assert status_bar(failed, 200).plain == (
            "gh core ?/? · graphql ?/? · reset --:-- | gh: graphql ⛔ timeout 14:04 100+ | gh poll 600s / 50% | 3/9 [███░░░░░░] 33% | active 14h20m | tick 14:04:31 | lead ·fenix"
        )
        truncated_ok = replace(value, gh=GhStatus("graphql", True, None, NOW, True))
        assert status_bar(truncated_ok, 200).plain == (
            "gh core ?/? · graphql ?/? · reset --:-- | gh: graphql ✅ 14:04 100+ | gh poll 600s / 50% | 3/9 [███░░░░░░] 33% | active 14h20m | tick 14:04:31 | lead ·fenix"
        )
        for width in (80, 120):
            assert cell_len(status_bar(value, width).plain) <= width
            assert cell_len(status_bar(failed, width).plain) <= width

    pacific_test(check)


def test_status_bar_reports_paused_quota_and_local_reset() -> None:
    def check() -> None:
        reset = datetime(2026, 10, 14, 15, 14, tzinfo=UTC)
        gh = replace(GhStatus("graphql", False, None, NOW, False), paused=True,
                     quota_remaining=2450, quota_limit=5000, quota_reset=reset,
                     poll_interval_s=120, quota_threshold=50)
        text = status_bar(replace(snapshot(), gh=gh), 160).plain
        assert "GitHub paused: 2450/5000 graphql remaining, below 50 percent, resets 08:14" in text
        assert "gh poll 120s / 50%" in text
        assert "graphql 2450/5000 ⏸ · reset 08:14" in status_bar(replace(snapshot(), gh=gh), 80).plain
    pacific_test(check)


def test_both_quotas_local_earliest_reset_threshold_and_staleness() -> None:
    def check() -> None:
        gh = replace(GhStatus("rest", True, None, NOW, False),
                     core_remaining=4999, core_limit=5000, core_reset=NOW + timedelta(hours=2),
                     graphql_remaining=2310, graphql_limit=5000, graphql_reset=NOW + timedelta(hours=1),
                     quota_at=NOW, poll_interval_s=600)
        expected = "gh core 4999/5000 · graphql 2310/5000 ⏸ · reset 15:04"
        assert github_quotas(gh, NOW + timedelta(seconds=600)) == expected
        assert github_quotas(gh, NOW + timedelta(seconds=601)) == expected + " · age 10m"
        assert status_bar(replace(snapshot(), gh=gh), 80).plain.startswith(expected)
        assert "3/9" in status_bar(replace(snapshot(), gh=gh, metrics=Metrics(3, 9, 33, 0)), 120).plain
        limited = replace(gh, graphql_remaining=4000, paused=True, rate_limited=True)
        assert "core 4999/5000 ⏸ · graphql 4000/5000" in github_quotas(limited, NOW)
        equal = replace(gh, graphql_remaining=2500)
        assert "⏸" not in github_quotas(equal, NOW)
    pacific_test(check)


def test_error_details_preserve_variant_fields() -> None:
    cases = (
        (CommandFailed("command_failed", ("gh", "pr"), 2, "denied", "retry"), "gh pr (exit 2): denied"),
        (Timeout("timeout", ("gh", "pr"), 20, "retry"), "gh pr timed out after 20s"),
        (Rejected("rejected", ("atm",), 403, "forbidden", "reauth"), "atm (status 403): forbidden"),
        (Unreachable("unreachable", ("atm",), "offline", "retry"), "atm: offline"),
        (ParseFailed("parse_failed", "snapshot.json", "bad JSON", "repair"), "snapshot.json: bad JSON"),
        (NotFound("not_found", "mmdflux", "install"), "mmdflux"),
        (ConfigInvalid("config_invalid", "interval_s", "zero", "configure"), "interval_s=zero"),
        (LockHeld("lock_held", 47, "wait"), "snapshot age 47s"),
        (LockHeld("lock_held", None, "wait"), "snapshot age unknown"),
        (IoFailed("io_failed", "state.json", "permission denied", "repair"), "state.json: permission denied"),
    )
    for error, detail in cases:
        rendered = _error_text(error)
        assert rendered == f"{error.kind}: {detail}; {error.fix}"


@dataclass(frozen=True)
class NewVariant:
    kind: str = "new_variant"
    fix: str = "add a branch"


def test_unknown_error_variant_fails_loudly_instead_of_falling_through() -> None:
    with pytest.raises(TypeError, match="unknown error variant NewVariant"):
        _error_text(NewVariant())


def test_stopped_lines_and_age_boundaries() -> None:
    assert tuple(_age(age) for age in (45, 32 * 60, 64 * 60, 2 * 86400)) == ("45s", "32m", "1h04", "2d")

    def check() -> None:
        for name, expected in (("snapshot-stopped-closed.json", "stopped: phase closed 14:04"),
                               ("snapshot-stopped-idle.json", "stopped: idle 60 min")):
            decoded = from_json(Snapshot, (FIXTURES / "contract" / name).read_text())
            assert isinstance(decoded, Ok)
            value = replace(decoded.value, generated_at=NOW)
            assert dashboard(value, 80, 10).plain.splitlines()[1] == expected

    pacific_test(check)


def test_running_contract_snapshot_renders_its_rows_unmodified() -> None:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    rendered = dashboard(decoded.value, 120, 40).plain

    assert rendered.splitlines()[0].startswith("phase-bh ·")
    assert "● ·arch-ctm  2 ⚠️1" in rendered
    assert "bh-6                    7m ⚠️bh-6: QA bh-6 round 2" in rendered
    assert "== Errors (2)" in rendered
    assert "command_failed: gh api graphql (exit 1): HTTP 502" in rendered
    assert "lock_held: snapshot age 42s" in rendered
    assert "== Blocked (1)" in rendered
    assert "CI failing on the top layer PR #1850" in rendered
    assert "bh-6.qa → ·quality-mgr" in rendered


def test_stopped_snapshot_renders_beads_and_pr_history() -> None:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-stopped-closed.json").read_text())
    assert isinstance(decoded, Ok)

    rendered = dashboard(decoded.value, 120, 40).plain

    assert "stopped: phase closed" in rendered
    assert "== Beads (1)" in rendered
    assert "bh-6.qa" in rendered
    assert "== PRs (1)" in rendered
    assert "PR #1850" in rendered
    assert "== Sent" in rendered


def test_stopped_snapshot_renders_unknown_bead_status_icon() -> None:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-stopped-closed.json").read_text())
    assert isinstance(decoded, Ok)
    sprint = decoded.value.sprints[0]
    step = replace(sprint.steps[0], status=SprintState.UNKNOWN)
    value = replace(decoded.value, sprints=(replace(sprint, steps=(step,)),))

    rendered = dashboard(value, 120, 40).plain

    assert "bh-6.qa" in rendered
    assert "? unknown" in rendered


def _single_row_dashboard(step_value: Step, width: int) -> list[str]:
    task = Task(step_value.bead_id, "cipher", "active", 0, NOW, NOW, "desc")
    phase = Phase(step_value.top_epic or "", "phase-bi", "Phase BI", False, (), (step_value,), ())
    agents = _agent_views((Member("cipher", "active", NOW),), (task,), phase, Ok(()), NOW, "atm")
    value = replace(snapshot(), agents=agents, errors=(), sections=(), sent=())
    return dashboard(value, width, 40).plain.splitlines()


@pytest.mark.parametrize("width", (60, 100, 160))
def test_agent_row_cuts_only_the_title_to_pane_width(width: int) -> None:
    title = ("fit the title " * 30)[:300]
    step = replace(STEP, bead_id="atm-bi-6.group-dev", kind="dev", sprint="bi-6", title=title, tag="phase-bi",
                   top_epic="atm-phase-bi", carry_forward=None)
    row = next(line for line in _single_row_dashboard(step, width) if "bi-6.group-dev" in line)

    assert cell_len(row) <= width
    assert row.endswith("…")
    assert "0s bi-6: fit the title" in row
    assert row.startswith(" \U000f051f💎 bi-6.group-dev"), width


def test_agent_row_that_fits_is_unchanged_by_pane_width() -> None:
    step = replace(STEP, bead_id="atm-bi-6.group-dev", kind="dev", sprint="bi-6", title="Fix the row prefix",
                   tag="phase-bi", top_epic="atm-phase-bi", carry_forward=None)
    narrow = next(line for line in _single_row_dashboard(step, 100) if "bi-6.group-dev" in line)
    wide = next(line for line in _single_row_dashboard(step, 240) if "bi-6.group-dev" in line)

    assert narrow == wide
    assert narrow.endswith("bi-6: Fix the row prefix")


def test_agent_row_prefix_for_a_fix_step_is_its_bead_short_id_not_the_phase_tag() -> None:
    fix = replace(STEP, bead_id="atm-phase-bi.10", kind="fix", sprint=None, sprint_bead=None, carry_forward=None,
                  title="Fix the row prefix", tag="phase-bi", top_epic="atm-phase-bi")
    phase = Phase("atm-phase-bi", "phase-bi", "Phase BI", False, (), (fix,), ())
    task = Task("atm-phase-bi.10", "cipher", "active", 0, NOW, NOW, "desc")
    agents = _agent_views((Member("cipher", "active", NOW),), (task,), phase, Ok(()), NOW, "atm")
    value = replace(snapshot(), agents=agents, errors=(), sections=(), sent=())

    assert agents[0].rows[0].tag == "phase-bi.10"
    text = dashboard(value, 160, 40).plain
    assert "phase-bi.10: Fix the row prefix" in text
    assert "phase-bi: " not in text
