from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.cells import cell_len

from atm_bd_console.codec import from_json
from atm_bd_console.render import _age, dashboard, status_bar
from atm_bd_console.result import Ok, Timeout
from atm_bd_console.types import (
    Action,
    AgentView,
    GhStatus,
    Metrics,
    PrRow,
    PullRequest,
    Row,
    Section,
    SectionRow,
    Snapshot,
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
        assert "● busy  4  ❗1 ⚠️1" in text
        assert "🌀 running" in text and "💎 hard" in text and "📥 normal" in text
        assert "… +1 more" in text and "⚡ fast" not in text
        assert "◐ stalling" in text and "🔍 sprint-qa" in text
        assert "■ blocked" in text and "🛠️ fix-qa" in text
        assert "○ offline" in text and "· idle" in text
        assert "❗bh: description for atm-hard" in text
        assert "⚠️bh: description for atm-normal" in text
        styles = {span.style for span in rendered.spans}
        assert {"green", "yellow", "red", "dim", "bold"} <= styles

    pacific_test(check)


def test_sections_error_first_sent_last_height_and_links() -> None:
    error = Timeout("timeout", ("atm",), 5, "retry")
    pr = PullRequest(553, "https://example/pr/553", "head", "base", "sha", "pending", ())
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
        assert text.count("→ dev-sanity") == 5
        assert any("link https://example/pr/553" in str(span.style) for span in rendered.spans)
        clipped = dashboard(value, 80, 10).plain.splitlines()
        assert len(clipped) == 10 and clipped[-1].startswith("… ") and "more rows" in clipped[-1]
        for line in dashboard(value, 80, 40).plain.splitlines():
            assert cell_len(line) <= 80

    pacific_test(check)


def test_status_bar_formats_and_cell_width() -> None:
    value = replace(snapshot(), metrics=Metrics(3, 9, 33, 14 * 3600 + 20 * 60),
                    gh=GhStatus("graphql", True, None, NOW, False))

    def check() -> None:
        expected = (FIXTURES / "render" / "status-bar-ok.txt").read_text().rstrip("\n")
        assert status_bar(value, 120).plain == expected
        assert status_bar(replace(value, gh=GhStatus("off", None, None, None, False)), 120).plain.startswith("gh: off |")
        failed = replace(value, gh=GhStatus("graphql", False, Timeout("timeout", ("gh",), 5, "retry"), NOW, True))
        assert status_bar(failed, 120).plain.startswith("gh: graphql ⛔ timeout 14:04 100+ |")
        assert cell_len(status_bar(value, 80).plain) <= 80

    pacific_test(check)


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
