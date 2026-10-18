from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeStep, run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE, TIMEOUT
from rich.cells import cell_len
from textual import events

from atm_bd_console import app as console
from atm_bd_console.app import ConsoleApp, run
from atm_bd_console.build import display_identity, identity
from atm_bd_console.enums import SprintState
from atm_bd_console.result import Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import Config, Snapshot, SprintView, State, StepView


def test_dispatch_uses_shared_status_rows(monkeypatch):
    from atm_bd_console import status_table

    values = status_table.rows(RUNNING)
    seen = []
    real_rows = status_table.rows

    def shared_rows(snapshot):
        seen.append(snapshot)
        return real_rows(snapshot)

    class Node:
        size = SimpleNamespace(width=120, height=40)

        def update(self, value):
            self.value = value

    nodes = {}

    def query(selector, *_args):
        return nodes.setdefault(selector, Node())

    dummy = SimpleNamespace(snapshot=RUNNING, tick_error=None, size=Node.size, query_one=query,
                            show_pr_history=False, status_error=None, status_text="", diagnostics_path="log",
                            input_log_path="input", diagnostics=[], call_after_refresh=lambda *_: None,
                            _refresh_status_viewport=lambda: None)
    monkeypatch.setattr(status_table, "rows", shared_rows)
    ConsoleApp.refresh_views(dummy)
    assert seen == [RUNNING]
    rendered = nodes["#status-table"].value
    assert list(rendered.columns[0]._cells) == [row.sprint for row in values]


class RaisingStep:
    def __call__(self, *_args) -> tuple[Snapshot, State]:
        raise RuntimeError("step exploded")


class BlockingStep(FakeStep):
    def __init__(self) -> None:
        super().__init__(RUNNING)
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def __call__(self, config: Config, state: State, force_github: bool) -> tuple[Snapshot, State]:
        self.calls.append(force_github)
        self.started.set()
        assert self.release.wait(2), "test must release the blocked step"
        self.completed.set()
        return self.snapshot, state


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


def test_raised_tick_failure_is_visible_in_diagnostics_and_written_to_its_log(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = replace(CONFIG, state_dir=str(tmp_path / "state"))
        app = ConsoleApp(config, RaisingStep(), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "diagnostics"
            await settle(pilot)
            shown = app.query_one("#diagnostics-text").render().plain
            assert "tick: io_failed" in shown and str(app.diagnostics_path) in shown
            assert "step exploded" in app.diagnostics_path.read_text()

    run_scenario(scenario())


def test_snapshot_source_failure_is_visible_in_diagnostics_and_written_to_its_log(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = replace(CONFIG, state_dir=str(tmp_path / "state"))
        app = ConsoleApp(config, FakeStep(replace(RUNNING, errors=(TIMEOUT,))), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "diagnostics"
            await settle(pilot)
            shown = app.query_one("#diagnostics-text").render().plain
            assert "source: timeout" in shown
            assert "source: timeout" in app.diagnostics_path.read_text()

    run_scenario(scenario())


def test_build_line_sits_right_justified_on_the_tab_header_row_and_never_covers_a_tab() -> None:
    async def scenario() -> None:
        app = ConsoleApp(CONFIG, FakeStep(RUNNING), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            build = app.query_one("#build")
            text = f"atm-bd console {display_identity()}"
            assert build.render().plain == text and build.display
            assert build.region.y == 0 and build.region.right == 120
            tabs_right = max(tab.region.right for tab in app.query("ContentTab"))
            assert build.region.x > tabs_right
            assert app.query_one("#--content-tab-dispatch").region.y == 0  # tab headers moved up: no row above them
            assert app.query_one("#dashboard").region.y == 2  # tabs, underline, content
            await pilot.resize_terminal(80, 30)
            await settle(pilot)
            assert build.display and build.region.right == 80 and build.region.x > tabs_right
            await pilot.resize_terminal(40, 30)  # no room right of the tab strip: hidden, every tab clickable
            await settle(pilot)
            assert not build.display
            await pilot.click("#--content-tab-diagnostics")
            await settle(pilot)
            assert app.query_one("TabbedContent").active == "diagnostics"
            shown = app.query_one("#diagnostics-text").render().plain
            assert f"Build: {identity()}" in shown
            assert "Agents: ·arch-ctm" in shown
            await pilot.resize_terminal(120, 40)
            await settle(pilot)
            assert build.display and build.region.right == 120

    run_scenario(scenario())


def test_raised_multiline_error_fits_dashboard_and_header_after_resize() -> None:
    class LongRaisingStep:
        def __call__(self, *_args) -> tuple[Snapshot, State]:
            raise RuntimeError("first error detail is far too long for a narrow terminal\nsecond line should be normalized")

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, LongRaisingStep(), STATE)
        async with app.run_test(size=(120, 24)) as pilot:
            await settle(pilot)
            dashboard = app.query_one("#dashboard")
            shown = dashboard.render().plain.splitlines()
            assert len(shown) == 2
            assert "first error detail" in shown[1]
            assert "second line should be normalized" in shown[1]
            assert max(cell_len(line) for line in shown) <= app.size.width

            await pilot.resize_terminal(18, 10)
            app.query_one("TabbedContent").active = "status"
            await pilot.pause()
            dashboard = app.query_one("#dashboard")
            shown = dashboard.render().plain.splitlines()
            assert max(cell_len(line) for line in shown) <= app.size.width
            header = app.query_one("#status-header")
            assert max(cell_len(line) for line in header.render().plain.splitlines()) <= app.size.width
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
                assert await asyncio.to_thread(step.started.wait, 1), "blocked step never started"
                app._on_interval()
                app._on_interval()
                await pilot.pause()
                assert step.calls == [False]
                step.release.set()
                assert await asyncio.to_thread(step.completed.wait, 1), "blocked step never completed"
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

    def step(_config: Config, state: State, force_github: bool) -> tuple[Snapshot, State]:
        calls.append(force_github)
        if len(calls) == 2:
            second_step_done.set()
            return second, state
        return first, state

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
            assert release_render.wait(2), "test must release the blocked renderer"
        return snapshot.phase, None

    monkeypatch.setattr(console.status, "status_text", slow_render)
    monkeypatch.setattr(ConsoleApp, "_show", show)

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test() as pilot:
            try:
                assert await asyncio.to_thread(render_started.wait, 1), "renderer never started"
                assert app._step_lock.acquire(blocking=False)
                app._step_lock.release()
                app.action_refresh()
                app._on_interval()
                release_render.set()
                assert await asyncio.to_thread(second_step_done.wait, 1), "second step never ran"
                assert await asyncio.to_thread(second_shown.wait, 1), "second snapshot never shown"
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
            assert acquire_lock(config.repo_root, "bh").error.kind == "lock_held"
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
            app.query_one("TabbedContent").active = "prs"
            await settle(pilot)
            await pilot.press("r")
            await settle(pilot)
            app.query_one("TabbedContent").active = "dispatch"
            await settle(pilot)
            assert step.calls == [False, True, True]

    run_scenario(scenario())


@pytest.mark.parametrize("all_closed", (False, True))
def test_status_initial_sprint_has_steps_and_refresh_preserves_user_selection(all_closed: bool) -> None:
    sprints = tuple(SprintView(
        f"atm-bh-{number}", f"sprint {number}",
        SprintState.CLOSED if all_closed or number == 1 else SprintState.IN_PROGRESS, (),
        (StepView(f"atm-bh-{number}.dev", "dev", "closed" if all_closed else "in_progress", "solar"),),
    ) for number in (1, 2, 3))
    step = FakeStep(replace(RUNNING, sprints=sprints))

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            selected = 0 if all_closed else 1
            options = app.query_one("#sprints")
            assert options.highlighted == selected
            shown = app.query_one("#sprint-steps-text").render().plain
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
            shown = app.query_one("#sprint-steps-text").render().plain
            assert "updated sprint" in shown
            assert sprints[2].steps[0].bead_id in shown

    run_scenario(scenario())


@pytest.mark.parametrize("size", ((140, 40), (100, 30)))
def test_status_graph_region_is_stable_above_two_column_sprint_details(size: tuple[int, int]) -> None:
    two_steps = tuple(StepView(f"atm-bh-2.dev-{number}", "dev", "closed", "solar") for number in range(2))
    six_steps = tuple(StepView(f"atm-bh-6.dev-{number}", "dev", "open", "solar") for number in range(6))
    overflowing_steps = tuple(StepView(f"atm-bh-7.dev-{number}", "dev", "open", "solar") for number in range(14))
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
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            panel = app.query_one("#status-panel")
            initial = panel.region
            sprints_widget = app.query_one("#sprints")
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
            assert "atm-bh-6.dev-5" in details_text.render().plain
            await pilot.press("end")
            await settle(pilot)
            assert panel.region == initial
            assert "atm-bh-7.dev-13" in details_text.render().plain
            assert details.max_scroll_y > 0

    run_scenario(scenario())


def _terminal_press(x: int, y: int) -> tuple[events.MouseDown, events.MouseUp]:
    # What the terminal driver posts for a click at cell (x, y); the pilot bypasses the app's event entry.
    return (events.MouseDown(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y),
            events.MouseUp(None, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y))


def test_input_trace_records_terminal_presses_keys_and_tab_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, state_dir=str(tmp_path / "state"), interval_s=3600), FakeStep(), STATE)
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
            shown = app.query_one("#diagnostics-text").render().plain
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
        assert not any(line.startswith("input trace:") for line in app.diagnostics)

    run_scenario(scenario())
