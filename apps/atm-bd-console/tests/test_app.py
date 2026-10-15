from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE, TIMEOUT

from atm_bd_console import app as console
from atm_bd_console.app import ConsoleApp, run
from atm_bd_console.result import Ok
from atm_bd_console.state import acquire_lock
from atm_bd_console.types import Config, Snapshot, State


class FakeStep:
    """A StepFn that records each force_github flag and returns its current snapshot."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[bool] = []

    def __call__(self, config: Config, state: State, force_github: bool, _store=None) -> tuple[Snapshot, State]:
        self.calls.append(force_github)
        return self.snapshot, state


class RaisingStep:
    def __call__(self, *_args) -> tuple[Snapshot, State]:
        raise RuntimeError("step exploded")


@pytest.fixture(autouse=True)
def no_mmdflux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console.status, "status_text", lambda snapshot: (snapshot.mermaid, None))


async def settle(pilot) -> None:
    try:
        await asyncio.wait_for(pilot.app.workers.wait_for_complete(), timeout=5)
    except TimeoutError as error:
        raise AssertionError("Textual workers did not complete within 5 seconds") from error
    await pilot.pause()


async def _bounded(coro):
    try:
        return await asyncio.wait_for(coro, timeout=5)
    except TimeoutError as error:
        raise AssertionError("Textual test scenario did not complete within 5 seconds") from error


def run_scenario(coro):
    return asyncio.run(_bounded(coro))


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


def test_q_quits_and_restores_the_terminal() -> None:
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
            assert step.calls.count(True) == 2
            assert step.calls[0] is False

    run_scenario(scenario())
