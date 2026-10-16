from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import FakeStep, run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE, TIMEOUT
from rich.cells import cell_len

from atm_bd_console import app as console
from atm_bd_console.app import ConsoleApp, run
from atm_bd_console.result import Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import Config, Snapshot, SprintView, State, StepView


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
            await asyncio.wait_for(asyncio.to_thread(step.started.wait), timeout=1)
            app._on_interval()
            app._on_interval()
            await pilot.pause()
            assert step.calls == [False]
            step.release.set()
            await asyncio.wait_for(asyncio.to_thread(step.completed.wait), timeout=1)
            await settle(pilot)
            assert step.calls == [False]

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
            await asyncio.wait_for(asyncio.to_thread(render_started.wait), timeout=1)
            assert app._step_lock.acquire(blocking=False)
            app._step_lock.release()
            app.action_refresh()
            app._on_interval()
            release_render.set()
            await asyncio.wait_for(asyncio.to_thread(second_step_done.wait), timeout=1)
            await asyncio.wait_for(asyncio.to_thread(second_shown.wait), timeout=1)
            await settle(pilot)
            assert calls == [False, True]
            assert rendered == [first.phase, "second"]
            assert shown == [first.phase, "second"]
            assert app.snapshot is second

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
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / ".atm-bd/atm-bd-console/bh"))
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
    config = replace(CONFIG, repo_root=str(tmp_path), state_dir=str(tmp_path / ".atm-bd/atm-bd-console/bh"))
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


def test_real_bh_status_graph_anchors_root_and_preserves_pan_on_refresh(monkeypatch) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/bh-wide-dag.json").read_text())
    graph = fixture["text"]
    assert hashlib.sha256(graph.encode()).hexdigest() == fixture["sha256"]
    assert max(cell_len(line) for line in graph.splitlines()) == 508
    monkeypatch.setattr(console.status, "status_text", lambda _snapshot: (graph, None))

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(RUNNING), STATE)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            panel = app.query_one("#status-panel")
            assert panel.size.width == 0
            assert panel.scroll_x == 0
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            assert app.query_one("#status-text").render().plain == graph
            assert panel.scroll_x == 204
            viewport = panel.scrollable_content_region
            root_left = viewport.x + 209 - int(panel.scroll_x)
            assert viewport.x <= root_left
            assert root_left + cell_len("atm-bh-1") <= viewport.right
            rendered = [strip.text for strip in app.screen._compositor.render_strips()]
            assert any("atm-bh-1" in line for line in rendered[viewport.y:viewport.bottom])
            assert app.query_one("#status-overflow").display

            panel.focus()
            await pilot.press("ctrl+pagedown")
            await app.animator.wait_for_idle()
            panned = panel.scroll_x
            assert panned > 204
            await pilot.press("r")
            await settle(pilot)
            assert panel.scroll_x == panned
            app.query_one("TabbedContent").active = "dispatch"
            await pilot.pause()
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            assert panel.scroll_x == panned

            await pilot.resize_terminal(540, 40)
            await settle(pilot)
            assert not app.query_one("#status-overflow").display
            assert panel.max_scroll_x == 0
            assert app.query_one("#status-text").render().plain == graph

    run_scenario(scenario())


@pytest.mark.parametrize("all_closed", (False, True))
def test_status_initial_sprint_has_steps_and_refresh_preserves_user_selection(all_closed: bool) -> None:
    sprints = tuple(SprintView(
        f"atm-bh-{number}", f"sprint {number}",
        "closed" if all_closed or number == 1 else "in_progress", (),
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
            shown = app.query_one("#sprint-steps").render().plain
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
            shown = app.query_one("#sprint-steps").render().plain
            assert "updated sprint" in shown
            assert sprints[2].steps[0].bead_id in shown

    run_scenario(scenario())
