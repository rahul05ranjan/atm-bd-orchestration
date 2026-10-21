from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from types import SimpleNamespace
from typing import Self, cast

import pytest
from conftest import HANG_GUARD_S, FakeStep, run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, SNAPSHOT, STATE, TIMEOUT
from rich.cells import cell_len
from sc_observability import Level
from textual import events
from textual.widgets import OptionList, TabbedContent
from type_helpers import text_of

from atm_bd_console import app as console
from atm_bd_console import prs, render
from atm_bd_console.app import (
    _QUALITY_BLOCKING_COLUMN,
    _QUALITY_IMPORTANT_COLUMN,
    _QUALITY_MINOR_COLUMN,
    _QUALITY_QA_COLUMN,
    ConsoleApp,
    run,
)
from atm_bd_console.build import display_identity
from atm_bd_console.enums import SprintState
from atm_bd_console.error_text import error_text
from atm_bd_console.held import Held
from atm_bd_console.log import create_console_logger
from atm_bd_console.poll_health import PollHealth
from atm_bd_console.result import Err, Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import Config, GhStatus, Snapshot, SprintView, State, StepResult, StepView


@dataclass
class DiagnosticEvent:
    timestamp: str
    level: Level
    message: str | None


def test_dispatch_uses_shared_status_rows(monkeypatch, tmp_path: Path):
    from atm_bd_console import status_table

    values = status_table.rows(RUNNING)
    seen = []
    real_rows = status_table.rows

    def shared_rows(snapshot):
        seen.append(snapshot)
        return real_rows(snapshot)

    class Node:
        size = SimpleNamespace(width=120, height=40)
        active = "dispatch"
        highlighted = None

        def update(self, value):
            self.value = value

        def clear_options(self):
            pass

        def add_options(self, _options):
            pass

    nodes = {}

    def query(selector, *_args):
        return nodes.setdefault(selector, Node())

    dummy = SimpleNamespace(snapshot=RUNNING, tick_error=None, size=Node.size, query_one=query,
                            config=replace(CONFIG, repo_root=str(tmp_path)), prs_read_at=None, sanity_held=Held(), open_quality_held=Held(), closed_quality_held=Held(), quality_runs=[], health=PollHealth(),
                            show_pr_history=False, status_error=None, status_text="",
                            input_log_path="input", call_after_refresh=lambda *_: None,
                            _refresh_status_viewport=lambda: None)
    monkeypatch.setattr(status_table, "rows", shared_rows)
    ConsoleApp.refresh_views(cast(ConsoleApp, dummy))
    assert seen == [RUNNING]
    rendered = nodes["#status-table"].value
    assert list(rendered.columns[0]._cells) == [row.sprint for row in values]
    assert nodes["#status-table"].display is True
    dummy.snapshot = replace(RUNNING, phase_data=None, errors=(TIMEOUT,))
    ConsoleApp.refresh_views(cast(ConsoleApp, dummy))
    assert nodes["#status-table"].display is True  # a labelled placeholder, never hidden
    assert nodes["#status-table"].value.plain == f"Sprint status: unavailable - {error_text(TIMEOUT)}"
    assert f"beads unavailable: {error_text(TIMEOUT)}" in nodes["#dashboard"].value.plain
    assert seen == [RUNNING]
    dummy.snapshot = RUNNING
    ConsoleApp.refresh_views(cast(ConsoleApp, dummy))
    assert nodes["#status-table"].display is True
    assert seen == [RUNNING, RUNNING]
    dummy.sanity_held = Held().apply(Err(TIMEOUT), datetime.now(UTC))
    dummy.open_quality_held = Held().apply(Err(TIMEOUT), datetime.now(UTC))
    ConsoleApp.refresh_views(cast(ConsoleApp, dummy))
    assert nodes["#quality-error"].display is True
    assert error_text(TIMEOUT) in nodes["#quality-error"].value.plain
    assert "Sanity runs: not loaded" in nodes["#sanity-list"].value.plain
    assert error_text(TIMEOUT) in nodes["#sanity-list"].value.plain
    assert "Timeout(" not in nodes["#sanity-list"].value.plain


def test_quality_reads_are_requested_only_on_open_or_explicit_refresh() -> None:
    calls = []
    active = SimpleNamespace(active="dispatch")
    dummy = SimpleNamespace(
        _trace_input=lambda *_: None,
        _load_closed_quality=lambda: calls.append("quality"),
        tick=lambda force: calls.append(("tick", force)),
        query_one=lambda *_: active,
        closed=SimpleNamespace(drop_stage_lists=lambda: calls.append("relist")),
    )
    ConsoleApp.on_tabbed_content_tab_activated(
        cast(ConsoleApp, dummy), cast(TabbedContent.TabActivated, SimpleNamespace(pane=SimpleNamespace(id="quality")))
    )
    assert calls == ["quality"]
    calls.clear()
    ConsoleApp.action_refresh(cast(ConsoleApp, dummy))
    assert calls == [("tick", True), "relist"]
    calls.clear()
    active.active = "quality"
    ConsoleApp.action_refresh(cast(ConsoleApp, dummy))
    assert calls == [("tick", True), "relist", "quality"]


@pytest.mark.parametrize("size", [(100, 40), (160, 40)])
def test_quality_runs_render_aligned_verdict_and_severity_columns(monkeypatch, tmp_path: Path, size) -> None:
    qa_pass = {"id": "qa-pass", "status": "closed", "started_at": "2026-10-02T13:00:00Z",
               "closed_at": "2026-10-02T13:01:00Z", "close_reason": "PASS",
               "metadata": {"sprint": "bj-4", "round": 1}, "checked_bead_data": {"metadata": {"pr_number": 42}}}
    qa_fail = {"id": "qa-fail-with-a-long-id", "status": "closed", "started_at": "2026-10-02T13:02:00Z",
               "closed_at": "2026-10-02T13:12:00Z", "close_reason": "FAIL findings",
               "metadata": {"sprint": "bj-12-long", "round": 12}, "checked_bead_data": {"metadata": {"pr_number": 123456}}}
    qa_blocking = {"id": "qa-fail-blocking", "status": "closed", "started_at": "2026-10-02T13:02:00Z",
                   "closed_at": "2026-10-02T13:12:00Z", "close_reason": "FAIL findings",
                   "metadata": {"sprint": "bj-12", "round": 3}}
    qa_no_verdict = {"id": "qa-unverdict", "status": "closed", "started_at": "2026-10-02T13:13:00Z",
                     "closed_at": "2026-10-02T13:14:00Z", "close_reason": "rebase required",
                     "metadata": {"sprint": "bj-4", "round": 2}}
    findings = [
        {"id": "f-blocking", "metadata": {"severity": "blocking"}, "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-fail-blocking"}]},
        {"id": "f-important", "metadata": {"severity": "important"}, "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-fail-with-a-long-id"}]},
        {"id": "f-minor-1", "metadata": {"severity": "minor"}, "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-fail-with-a-long-id"}]},
        {"id": "f-minor-2", "metadata": {"severity": "minor"}, "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-fail-with-a-long-id"}]},
    ]
    quality_runs = console.runs.qa_runs([qa_pass, qa_fail, qa_blocking, qa_no_verdict])
    rendered = []
    monkeypatch.setattr(console.findings_renderer, "render_findings",
                        lambda _root, rows: rendered.extend(rows) or "# Findings")

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600, state_dir=str(tmp_path)),
                         FakeStep(SNAPSHOT, ([], quality_runs, findings)), STATE)
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "quality"
            await settle(pilot)
            header = text_of(app.query_one("#quality-runs-header").render())
            options = app.query_one("#quality-runs", OptionList)
            assert app.query_one("#quality-runs-header").content_region.x == options.content_region.x
            labels = {options.get_option_at_index(i).id: text_of(options.get_option_at_index(i).prompt)
                      for i in range(options.option_count)}
            header_fields = header.plain.split(" | ")
            assert [field.strip() for field in header_fields] == [
                "Completed", "QA", "Sprint", "QA bead", "PR", "Round", "B", "I", "M", "Elapsed",
            ]
            row_fields = {key: label.plain.split(" | ") for key, label in labels.items()}
            assert all(len(fields) == len(header_fields) for fields in row_fields.values())
            for index in range(len(header_fields)):
                widest = max(cell_len(fields[index]) for fields in [header_fields, *row_fields.values()])
                assert all(cell_len(fields[index]) == widest for fields in [header_fields, *row_fields.values()])

            fail_fields = row_fields["qa-fail-with-a-long-id"]
            severity_columns = (_QUALITY_BLOCKING_COLUMN, _QUALITY_IMPORTANT_COLUMN, _QUALITY_MINOR_COLUMN)
            assert [fail_fields[index].strip() for index in severity_columns] == ["", "1", "2"]
            assert row_fields["qa-pass"][_QUALITY_QA_COLUMN].strip().endswith("pass")
            assert fail_fields[_QUALITY_QA_COLUMN].strip().endswith("fail")
            assert row_fields["qa-fail-blocking"][_QUALITY_QA_COLUMN].strip().endswith("fail")
            assert row_fields["qa-unverdict"][_QUALITY_QA_COLUMN].strip() == ""
            for key, glyph, color in (("qa-pass", "\U000f029b", "#2EA043"),
                                      ("qa-fail-with-a-long-id", "\U000f029b", "#FF9F80"),
                                      ("qa-fail-blocking", "\U000f029b", "#F85149")):
                label = labels[key]
                assert label.plain.split(" | ")[_QUALITY_QA_COLUMN].strip().split()[0] == glyph
                start = label.plain.index(glyph)
                assert any(span.start <= start < span.end and str(span.style) == color for span in label.spans)
            no_verdict = labels["qa-unverdict"]
            field = no_verdict.plain.split(" | ")[_QUALITY_QA_COLUMN]
            start = no_verdict.plain.index(field)
            assert field.isspace()
            assert not any(span.start < start + len(field) and span.end > start for span in no_verdict.spans)
            for label in labels.values():
                for field_index in severity_columns:
                    field_start = sum(cell_len(field) + 3 for field in label.plain.split(" | ")[:field_index])
                    field_end = field_start + cell_len(label.plain.split(" | ")[field_index])
                    assert not any(span.start < field_end and span.end > field_start for span in label.spans)

            options.focus()
            await pilot.press("down")
            await settle(pilot)
            assert options.highlighted == 1
            assert rendered and {row["id"] for row in rendered} == {"f-important", "f-minor-1", "f-minor-2"}

    run_scenario(scenario())


class RaisingStep:
    def __call__(self, *_args) -> StepResult:
        raise RuntimeError("step exploded")


class BlockingStep(FakeStep):
    def __init__(self) -> None:
        super().__init__(RUNNING)
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def __call__(self, config: Config, state: State, force_github: bool) -> StepResult:
        self.calls.append(force_github)
        self.started.set()
        assert self.release.wait(HANG_GUARD_S), "test must release the blocked step"
        self.completed.set()
        return StepResult(self.snapshot, state)


def test_error_snapshot_keeps_the_app_running_and_shows_the_error() -> None:
    step = FakeStep(replace(RUNNING, errors=(TIMEOUT,)))

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, step, STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.is_running
            shown = str(app.query_one("#dashboard").render())
            assert "== Errors (1)" in shown and "timeout" in shown and TIMEOUT.fix in shown
            await pilot.press("ctrl+q")
            await settle(pilot)
            assert app.is_running

    run_scenario(scenario())


def test_raised_step_keeps_the_app_running_and_shows_the_error() -> None:
    async def scenario() -> None:
        app = ConsoleApp(CONFIG, RaisingStep(), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.is_running
            shown = str(app.query_one("#dashboard").render())
            assert "== Errors (1)" in shown and "step exploded" in shown

    run_scenario(scenario())


def test_raised_tick_failure_is_logged_and_diagnostics_file_is_removed(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / "state"))
        app = ConsoleApp(config, RaisingStep(), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "diagnostics"
            app.refresh_views()
            await settle(pilot)
            shown = text_of(app.query_one("#diagnostics-text").render()).plain
            assert "error: `retry the console tick` io_failed step exploded" in shown
            assert not (tmp_path / "state" / "diagnostics.log").exists()

    run_scenario(scenario())


def test_diagnostics_view_reads_structured_log_entries(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    created = create_console_logger(repo_root)
    assert isinstance(created, Ok)
    assert created.value.event("error", level="error", message="`retry` timeout after 10s") == Ok(None)
    assert created.value.flush() == Ok(None)

    async def scenario() -> None:
        config = replace(CONFIG, repo_root=str(repo_root), state_dir=str(tmp_path / "state"))
        app = ConsoleApp(config, FakeStep(replace(RUNNING, errors=(TIMEOUT,))), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "diagnostics"
            app.refresh_views()
            await settle(pilot)
            shown = text_of(app.query_one("#diagnostics-text").render()).plain
            assert " error: `retry` timeout after 10s" in shown
            assert not (tmp_path / "state" / "diagnostics.log").exists()

    run_scenario(scenario())


def test_display_clocks_are_local_and_structured_log_stays_zulu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Etc/GMT+8")
    time.tzset()
    try:
        fixed_now = SNAPSHOT.generated_at

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> Self:
                return cls.fromtimestamp(fixed_now.timestamp(), tz or fixed_now.tzinfo)

        monkeypatch.setattr(console, "datetime", FixedDateTime)
        snapshot = replace(SNAPSHOT, generated_at=fixed_now)
        no_pr_snapshot = replace(snapshot, prs=(), gh=GhStatus("graphql", True, None, fixed_now, False))
        dashboard_header = render.dashboard(snapshot, 120, 40).plain.splitlines()[0]
        dispatch_row = render._sent_line(snapshot.sent[0], snapshot).plain
        pr_sheet = prs.render_prs(no_pr_snapshot, 120).plain
        assert dashboard_header.startswith("phase-bh · 04:00 ·")
        assert dispatch_row.startswith(" 04:00 ")
        assert "checked 04:00" in pr_sheet

        repo_root = tmp_path / "repo"
        logger = create_console_logger(repo_root)
        assert isinstance(logger, Ok)
        assert logger.value.event("error", level="error", message="fixed diagnostic event") == Ok(None)
        assert logger.value.flush() == Ok(None)
        events = logger.value.query(levels=("error",), limit=1, order="newest_first")
        assert isinstance(events, Ok) and len(events.value) == 1
        event = events.value[0]
        assert event.timestamp.endswith("Z")
        expected_clock = datetime.fromisoformat(event.timestamp).astimezone().strftime("%H:%M:%S")

        app = ConsoleApp(
            replace(CONFIG, repo_root=str(repo_root), state_dir=str(tmp_path / "state"), interval_s=3600),
            FakeStep(snapshot), STATE,
        )

        async def scenario() -> None:
            async with app.run_test(size=(120, 40)) as pilot:
                await settle(pilot)
                app.query_one("TabbedContent", TabbedContent).active = "diagnostics"
                app.refresh_views()
                await settle(pilot)
                shown = text_of(app.query_one("#diagnostics-text").render()).plain
                assert f"{expected_clock} error: fixed diagnostic event" in shown
                assert not (tmp_path / "state" / "diagnostics.log").exists()

        run_scenario(scenario())
        assert not hasattr(app, "_record_error")
        assert not hasattr(app, "diagnostics_path")
        assert app.input_log_path.read_text().startswith("2026-10-14T12:00:00.000Z start")
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        time.tzset()


def test_diagnostic_line_uses_local_clock_and_preserves_message(monkeypatch) -> None:
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Etc/GMT+8")
    time.tzset()
    try:
        event = DiagnosticEvent(
            timestamp="2026-10-18T12:00:00+00:00", level="error", message="`retry` gh api [1] rate limited",
        )
        assert console._diagnostic_line(event) == "04:00:00 error: `retry` gh api [1] rate limited"
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        time.tzset()


def test_build_line_sits_right_justified_on_the_tab_header_row_and_never_covers_a_tab() -> None:
    async def scenario() -> None:
        app = ConsoleApp(CONFIG, FakeStep(RUNNING), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            build = app.query_one("#build")
            text = f"atm-bd console {display_identity()}"
            assert text_of(build.render()).plain == text and build.display
            assert build.region.y == 0 and build.region.right == 120
            tabs_right = max(tab.region.right for tab in app.query("ContentTab"))
            assert build.region.x > tabs_right
            assert app.query_one("#--content-tab-dispatch").region.y == 0  # tab headers moved up: no row above them
            assert app.query_one("#dashboard").region.y == 2  # tabs, underline, content
            await pilot.resize_terminal(80, 30)
            await settle(pilot)
            assert not build.display
            assert all(tab.display and tab.region.right <= 80 for tab in app.query("ContentTab"))
            await pilot.resize_terminal(40, 30)  # the overflowing tab strip remains keyboard navigable
            await settle(pilot)
            assert not build.display
            from textual.widgets import Tabs
            app.query_one(Tabs).focus()
            await pilot.press("left")  # wrap from Dispatch to Diagnostics
            await settle(pilot)
            assert app.query_one("TabbedContent", TabbedContent).active == "diagnostics"
            shown = text_of(app.query_one("#diagnostics-text").render()).plain
            assert f"Build: {display_identity()}" in shown
            assert "Agents: ·arch-ctm" in shown
            await pilot.resize_terminal(120, 40)
            await settle(pilot)
            assert build.display and build.region.right == 120

    run_scenario(scenario())


def test_raised_multiline_error_fits_dashboard_and_header_after_resize() -> None:
    class LongRaisingStep:
        def __call__(self, *_args) -> StepResult:
            raise RuntimeError("first error detail is far too long for a narrow terminal\nsecond line should be normalized")

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, LongRaisingStep(), STATE)
        async with app.run_test(size=(120, 24)) as pilot:
            await settle(pilot)
            dashboard = app.query_one("#dashboard")
            shown = text_of(dashboard.render()).plain.splitlines()
            assert len(shown) == 2
            assert "first error detail" in shown[1]
            assert "second line should be normalized" in shown[1]
            assert max(cell_len(line) for line in shown) <= app.size.width

            await pilot.resize_terminal(18, 10)
            app.query_one("TabbedContent", TabbedContent).active = "status"
            await pilot.pause()
            dashboard = app.query_one("#dashboard")
            shown = text_of(dashboard.render()).plain.splitlines()
            assert max(cell_len(line) for line in shown) <= app.size.width
            header = app.query_one("#status-header")
            assert max(cell_len(line) for line in text_of(header.render()).plain.splitlines()) <= app.size.width
            assert "…" in shown[1]

    run_scenario(scenario())


def test_q_quits_the_app() -> None:
    async def scenario() -> None:
        app = ConsoleApp(CONFIG, FakeStep(RUNNING), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("q")
            await pilot.pause()
        assert not app.is_running
        assert app.return_code == 0

    run_scenario(scenario())


def test_stopped_snapshot_stops_step_calls_until_r() -> None:
    step = FakeStep(replace(RUNNING, stopped="idle"))
    slow = replace(CONFIG, interval_s=3600)

    async def scenario() -> None:
        app = ConsoleApp(slow, step, STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app._on_interval()
            await settle(pilot)
            assert step.calls == [False]
            step.snapshot = RUNNING
            await pilot.press("r")
            await settle(pilot)
            app._on_interval()
            await settle(pilot)
            assert step.calls == [False, True, False]

    run_scenario(scenario())


def test_interval_ticks_do_not_accumulate_behind_a_blocked_step() -> None:
    step = BlockingStep()

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test() as pilot:
            try:
                assert await asyncio.to_thread(step.started.wait, HANG_GUARD_S), "blocked step never started"
                app._on_interval()
                app._on_interval()
                await pilot.pause()
                assert step.calls == [False]
                step.release.set()
                assert await asyncio.to_thread(step.completed.wait, HANG_GUARD_S), "blocked step never completed"
                await settle(pilot)
                assert step.calls == [False]
            finally:
                step.release.set()

    run_scenario(scenario())


def test_forced_refresh_waits_for_slow_render_and_keeps_snapshot_order(monkeypatch) -> None:
    first = RUNNING
    second = replace(RUNNING, phase="second")
    render_started = threading.Event()
    release_render = threading.Event()
    second_step_done = threading.Event()
    second_shown = threading.Event()
    calls: list[bool] = []
    rendered: list[str] = []
    shown: list[str] = []

    def step(_config: Config, state: State, force_github: bool) -> StepResult:
        calls.append(force_github)
        if len(calls) == 2:
            second_step_done.set()
            return StepResult(second, state)
        return StepResult(first, state)

    original_show = ConsoleApp._show

    def show(app: ConsoleApp, snapshot: Snapshot, text: str, error) -> None:
        shown.append(snapshot.phase)
        original_show(app, snapshot, text, error)
        if snapshot is second:
            second_shown.set()

    def slow_render(snapshot: Snapshot) -> tuple[str, None]:
        rendered.append(snapshot.phase)
        if len(rendered) == 1:
            render_started.set()
            assert release_render.wait(HANG_GUARD_S), "test must release the blocked renderer"
        return snapshot.phase, None

    monkeypatch.setattr(console.status, "status_text", slow_render)
    monkeypatch.setattr(ConsoleApp, "_show", show)

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test() as pilot:
            try:
                assert await asyncio.to_thread(render_started.wait, HANG_GUARD_S), "renderer never started"
                assert app._step_lock.acquire(blocking=False)
                app._step_lock.release()
                app.action_refresh()
                app._on_interval()
                release_render.set()
                assert await asyncio.to_thread(second_step_done.wait, HANG_GUARD_S), "second step never ran"
                assert await asyncio.to_thread(second_shown.wait, HANG_GUARD_S), "second snapshot never shown"
                await settle(pilot)
                assert calls == [False, True]
                assert rendered == [first.phase, "second"]
                assert shown == [first.phase, "second"]
                assert app.snapshot is second
            finally:
                release_render.set()

    run_scenario(scenario())


def test_tick_that_lands_after_quit_is_ignored() -> None:
    # A tick in flight when q tears the screen down must not query the unmounted widgets.
    async def scenario() -> ConsoleApp:
        app = ConsoleApp(CONFIG, FakeStep(RUNNING), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("q")
            await pilot.pause()
        return app

    app = run_scenario(scenario())
    assert not app.is_running
    app._show(RUNNING, "", None)


def test_held_lock_exits_1_with_its_message_before_drawing(tmp_path, monkeypatch, capsys) -> None:
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / ".atm-bd/atm-bd/bh"))
    holder = acquire_lock(config.repo_root, "bh")
    assert isinstance(holder, Ok)
    drawn: list[object] = []
    monkeypatch.setattr(console, "ConsoleApp", lambda *args: drawn.append(args))
    step = FakeStep(RUNNING)
    with holder.value:
        assert run(config, step, STATE) == 1
    assert drawn == [] and step.calls == []
    err = capsys.readouterr().err
    assert "lock held" in err and "wait for the running console process" in err


def test_run_takes_the_lock_and_returns_the_app_code(tmp_path, monkeypatch) -> None:
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / ".atm-bd/atm-bd/bh"))
    seen: list[tuple] = []

    class Recorder:
        def __init__(self, *args) -> None:
            seen.append(args)

        def run(self) -> int:
            # While the app runs, a second console cannot take the lock.
            locked = acquire_lock(config.repo_root, "bh")
            assert isinstance(locked, Err)
            assert locked.error.kind == "lock_held"
            return 0

    monkeypatch.setattr(console, "ConsoleApp", Recorder)
    step = FakeStep(RUNNING)
    assert run(config, step, STATE) == 0
    assert seen[0][2] == STATE and seen[0][3] is None
    assert isinstance(acquire_lock(config.repo_root, "bh"), Ok)


def test_opening_prs_then_r_forces_github_exactly_twice() -> None:
    step = FakeStep(RUNNING)

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "prs"
            await settle(pilot)
            await pilot.press("r")
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "dispatch"
            await settle(pilot)
            assert step.calls == [False, True, True]

    run_scenario(scenario())


@pytest.mark.parametrize("all_closed", (False, True))
def test_status_initial_sprint_has_steps_and_refresh_preserves_user_selection(all_closed: bool) -> None:
    sprints = tuple(SprintView(
        f"atm-bh-{number}", f"sprint {number}",
        SprintState.CLOSED if all_closed or number == 1 else SprintState.IN_PROGRESS, (),
        (StepView(f"atm-bh-{number}.dev", "dev", SprintState.CLOSED if all_closed else SprintState.IN_PROGRESS, "solar"),),
    ) for number in (1, 2, 3))
    step = FakeStep(replace(RUNNING, sprints=sprints))

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "status"
            await settle(pilot)
            selected = 0 if all_closed else 1
            options = app.query_one("#sprints", OptionList)
            assert options.highlighted == selected
            shown = text_of(app.query_one("#sprint-steps-text").render()).plain
            assert sprints[selected].id in shown
            assert sprints[selected].steps[0].bead_id in shown

            options.focus()
            await pilot.press("end")
            await settle(pilot)
            assert options.highlighted == 2
            step.snapshot = replace(step.snapshot, sprints=(*sprints[:2], replace(sprints[2], title="updated sprint")))
            await pilot.press("r")
            await settle(pilot)
            assert options.highlighted == 2
            shown = text_of(app.query_one("#sprint-steps-text").render()).plain
            assert "updated sprint" in shown
            assert sprints[2].steps[0].bead_id in shown

    run_scenario(scenario())


@pytest.mark.parametrize("size", ((140, 40), (100, 30)))
def test_status_graph_region_is_stable_above_two_column_sprint_details(size: tuple[int, int]) -> None:
    two_steps = tuple(StepView(f"atm-bh-2.dev-{number}", "dev", SprintState.CLOSED, "solar") for number in range(2))
    six_steps = tuple(StepView(f"atm-bh-6.dev-{number}", "dev", SprintState.OPEN, "solar") for number in range(6))
    overflowing_steps = tuple(StepView(f"atm-bh-7.dev-{number}", "dev", SprintState.OPEN, "solar") for number in range(14))
    sprints = (
        SprintView("atm-bh-2", "bh-2: two steps", SprintState.READY, (), two_steps),
        SprintView("atm-bh-6", "bh-6: six steps", SprintState.IN_PROGRESS, (), six_steps),
        SprintView("atm-bh-7", "bh-7: overflowing steps", SprintState.READY, (), overflowing_steps),
    )
    step = FakeStep(replace(RUNNING, sprints=sprints, mermaid="graph TD\n    one --> two"))

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "status"
            await settle(pilot)
            panel = app.query_one("#status-panel")
            initial = panel.region
            sprints_widget = app.query_one("#sprints", OptionList)
            details = app.query_one("#sprint-steps")
            details_text = app.query_one("#sprint-steps-text")
            assert sprints_widget.region.width > 0 and details.region.width > 0
            assert sprints_widget.region.y == details.region.y
            assert details.region.height == 11
            assert panel.region.y < details.region.y and panel.region.bottom <= details.region.y
            assert sprints_widget.region.x < details.region.x
            sprints_widget.focus()
            await pilot.press("home")
            await settle(pilot)
            assert panel.region == initial
            await pilot.press("down")
            await settle(pilot)
            assert panel.region == initial
            assert "atm-bh-6.dev-5" in text_of(details_text.render()).plain
            await pilot.press("end")
            await settle(pilot)
            assert panel.region == initial
            assert "atm-bh-7.dev-13" in text_of(details_text.render()).plain
            assert details.max_scroll_y > 0

    run_scenario(scenario())


def _terminal_press(x: int, y: int) -> tuple[events.MouseDown, events.MouseUp]:
    # What the terminal driver posts for a click at cell (x, y); the pilot bypasses the app's event entry.
    return (events.MouseDown(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y),
            events.MouseUp(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y))


def test_input_trace_records_terminal_presses_keys_and_tab_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / "state"), interval_s=3600), FakeStep(), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            tab = app.query_one("#--content-tab-status").region
            for event in _terminal_press(tab.x + 2, tab.y):
                app.post_message(event)
            await settle(pilot)
            panel = app.query_one("#status-panel").region
            for event in _terminal_press(panel.x + 2, panel.y + 2):
                app.post_message(event)
            await pilot.press("d")
            await settle(pilot)
            shown = text_of(app.query_one("#diagnostics-text").render()).plain
        trace = [line.split(" ", 1)[1] for line in app.input_log_path.read_text().splitlines()]
        assert str(app.input_log_path) in shown
        assert trace[0].startswith("start size=120x40 driver=HeadlessDriver TERM=")
        assert f"mouse_down x={tab.x + 2} y={tab.y} button=1 widget=ContentTab#--content-tab-status active=dispatch" in trace
        assert "tab status" in trace
        assert f"mouse_up x={panel.x + 2} y={panel.y + 2} button=1 widget=StatusPanel#status-panel active=status" in trace
        assert "key d active=status" in trace and "tab diagnostics" in trace

    run_scenario(scenario())


def test_off_screen_presses_are_traced_without_raising(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, state_dir=str(tmp_path / "state"), interval_s=3600), FakeStep(), STATE)
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            for x, y in ((500, 500), (-1, -1)):
                for event in _terminal_press(x, y):
                    await app.on_event(event)
            await settle(pilot)
        trace = [line.split(" ", 1)[1] for line in app.input_log_path.read_text().splitlines()]
        assert "mouse_down x=500 y=500 button=1 widget=off-screen active=dispatch" in trace
        assert "mouse_up x=-1 y=-1 button=1 widget=off-screen active=dispatch" in trace
        assert not (tmp_path / "state" / "diagnostics.log").exists()

    run_scenario(scenario())


def test_build_line_for_a_release_install_is_the_package_version(monkeypatch) -> None:
    from atm_bd_console import __version__
    from atm_bd_console import build as build_module

    monkeypatch.setattr(build_module, "BUILT_IDENTITY", f"{__version__}+g846527b")
    monkeypatch.setattr(build_module, "_checkout_identity", lambda root: None)

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, FakeStep(RUNNING), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert text_of(app.query_one("#build").render()).plain == f"atm-bd console {__version__}"

    asyncio.run(scenario())


def test_startup_atm_members_error_is_not_repeated_by_the_tick_that_reads_the_same_command() -> None:
    from atm_bd_console.app import startup_error_applies
    from atm_bd_console.result import CommandFailed, NotFound

    startup = CommandFailed("command_failed", ("atm", "members", "--team", "t", "--json"), 1, "daemon down", "could not verify the sender; start the daemon")
    tick_error = replace(startup, fix="start the daemon")
    sender_missing = NotFound("not_found", "atm-bd@tool", "run atm teams add-member")
    live = RUNNING
    stopped = replace(RUNNING, stopped="closed")
    assert not startup_error_applies(startup, replace(live, errors=(tick_error,)))  # same argv: the tick shows it
    assert not startup_error_applies(startup, replace(live, errors=()))  # a live tick read members: resolved
    assert not startup_error_applies(startup, replace(stopped, errors=(tick_error,)))
    assert startup_error_applies(startup, replace(stopped, errors=()))  # a stopped tick reads no members
    assert startup_error_applies(sender_missing, replace(live, errors=(tick_error,)))  # a finding the tick never repeats

    async def scenario() -> None:
        shown = replace(SNAPSHOT, errors=(tick_error,))
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(shown), STATE, startup)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            assert app.snapshot is not None
            assert app.snapshot.errors == (tick_error,)

    run_scenario(scenario())


class _QueryLogger:
    """A logger whose query result is scripted; the other calls a tick makes are accepted and ignored."""

    def __init__(self, queried) -> None:
        self.queried = queried

    def query(self, **_kwargs):
        return self.queried

    def event(self, *_args, **_kwargs):
        return Ok(None)

    def flush(self):
        return Ok(None)


def _diagnostics_text(monkeypatch, tmp_path, logger_result) -> str:
    monkeypatch.setattr(console, "create_console_logger", lambda *_: logger_result)
    shown = []

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600, state_dir=str(tmp_path / "state")), FakeStep(RUNNING), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent", TabbedContent).active = "diagnostics"
            app.refresh_views()
            await settle(pilot)
            shown.append(text_of(app.query_one("#diagnostics-text").render()).plain)

    run_scenario(scenario())
    return shown[0]


def test_diagnostics_shows_the_typed_error_of_a_failed_log_query_not_no_errors(monkeypatch, tmp_path) -> None:
    from atm_bd_console.result import IoFailed

    failure = IoFailed("io_failed", "logs", "disk unreadable", "inspect the console log directory")
    text = _diagnostics_text(monkeypatch, tmp_path, Ok(_QueryLogger(Err(failure))))
    assert f"Unable to read the console log: {error_text(failure)}" in text and "No errors recorded." not in text
    text = _diagnostics_text(monkeypatch, tmp_path, Err(failure))
    assert f"Unable to open the console log: {error_text(failure)}" in text and "No errors recorded." not in text
    assert "No errors recorded." in _diagnostics_text(monkeypatch, tmp_path, Ok(_QueryLogger(Ok(()))))


def test_diagnostics_line_survives_a_malformed_stored_timestamp() -> None:
    event = DiagnosticEvent(timestamp="not-a-time", level="error", message="boom")
    assert console._diagnostic_line(event) == "not-a-time error: boom"


def test_sprint_steps_pane_is_never_blank_before_or_after_a_failed_first_tick() -> None:
    step = BlockingStep()

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, step, STATE)
        async with app.run_test() as pilot:
            assert step.started.wait(HANG_GUARD_S)
            assert "unavailable" in text_of(app.query_one("#sprint-steps-text").render()).plain
            step.release.set()
            await settle(pilot)

    run_scenario(scenario())

    async def failed() -> None:
        app = ConsoleApp(CONFIG, RaisingStep(), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            shown = text_of(app.query_one("#sprint-steps-text").render()).plain
            assert "unavailable" in shown and "step exploded" in shown

    run_scenario(failed())


def test_findings_render_failure_shows_the_typed_error_text(monkeypatch) -> None:
    from atm_bd_console.result import IoFailed

    def boom(_root, _rows):
        raise ValueError("renderer exploded")

    monkeypatch.setattr(console.findings_renderer, "render_findings", boom)
    shown: list[tuple[str, dict]] = []
    run = console.runs.qa_runs([{"id": "qa-1", "status": "closed", "close_reason": "FAIL",
                                 "started_at": "2026-10-02T13:00:00Z", "closed_at": "2026-10-02T13:01:00Z",
                                 "metadata": {"sprint": "bj-4", "round": 1}}])
    dummy = SimpleNamespace(quality_runs=run, finding_beads=[], snapshot=None, config=CONFIG,
                            _update_findings=lambda content, colors: shown.append((content, colors)))

    ConsoleApp._show_findings(cast(ConsoleApp, dummy), "qa-1")

    expected = error_text(IoFailed("io_failed", "findings rendering", "renderer exploded",
                                   "report the findings renderer as a defect; select the run again"))
    assert shown == [(f"Unable to render findings: {expected}", {})]
