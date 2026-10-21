"""Held values and the Sanity and Quality tabs: last good rows survive a failed read, and nothing is blank."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from conftest import FakeStep, run_scenario, settle
from contract_samples import CONFIG, SNAPSHOT, STATE, TIMEOUT
from textual.containers import VerticalScroll
from textual.widgets import Static

from atm_bd_console import ledger_source, runs
from atm_bd_console.app import ConsoleApp, quality_notes, sanity_pane
from atm_bd_console.error_text import error_text
from atm_bd_console.held import Held, held_view
from atm_bd_console.result import Err, Ok, ParseFailed

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=150)


def _runs(count: int) -> list[runs.SanityRun]:
    """Ledger rows in file order (oldest first); sanity_runs sorts them newest first."""
    return runs.sanity_runs([
        {"run_id": f"r{index}", "completed_at": f"2026-10-02T13:{index % 60:02d}:00Z", "sprint": f"bj-{index}",
         "reviewer": "sanity-jev", "verdict": "PASS"} for index in range(count)])


def _run(index: int) -> runs.SanityRun:
    return _runs(index + 1)[0]


def test_apply_keeps_the_value_on_a_failed_read_and_clears_the_error_on_a_good_one() -> None:
    good = Held().apply(Ok([1]), NOW)
    failed = good.apply(Err(TIMEOUT), LATER)
    assert failed == Held([1], NOW, TIMEOUT)
    assert failed.apply(None, LATER) == failed
    assert failed.apply(Ok([2]), LATER) == Held([2], LATER)


def test_held_view_states() -> None:
    assert held_view(Held(), "Runs", NOW) == (None, "Runs: not loaded yet")
    assert held_view(Held([1], NOW), "Runs", LATER) == ([1], "")
    value, note = held_view(Held(None, None, TIMEOUT), "Runs", LATER)
    assert value is None and note == f"Runs: not loaded - {error_text(TIMEOUT)}"
    value, note = held_view(Held([1], NOW, TIMEOUT), "Runs", LATER)
    assert value == [1] and note.startswith("Runs: stale 2m (last good ") and note.endswith(error_text(TIMEOUT))


def test_sanity_pane_without_data_is_a_labelled_placeholder_not_headers_only() -> None:
    main, errors = sanity_pane(Held(), (), NOW)
    assert main.plain == "Sanity runs: not loaded yet" and errors.plain == ""
    main, _ = sanity_pane(Held(None, None, TIMEOUT), (), NOW)
    assert error_text(TIMEOUT) in main.plain and "Timeout(" not in main.plain and "Completed" not in main.plain


def test_sanity_pane_keeps_the_last_rows_when_the_read_fails() -> None:
    held = Held([_run(1)], NOW, TIMEOUT)
    main, _ = sanity_pane(held, (), LATER, local_zone=UTC)
    assert "bj-1" in main.plain and "stale 2m" in main.plain and error_text(TIMEOUT) in main.plain
    assert "Timeout(" not in main.plain


def test_sanity_pane_empty_good_read_says_so() -> None:
    main, _ = sanity_pane(Held([], NOW), (), NOW)
    assert "No sanity runs recorded" in main.plain


def test_quality_notes_name_each_stale_part() -> None:
    fresh = Held(([], []), NOW)
    assert quality_notes(fresh, fresh, NOW) == ""
    notes = quality_notes(Held(([], []), NOW, TIMEOUT), Held(), LATER)
    assert notes.splitlines()[0].startswith("Open QA runs: stale 2m") and notes.splitlines()[1] == "Closed QA runs: not loaded yet"


def test_sanity_ledger_failures_are_typed_errors(monkeypatch) -> None:
    def broken(*_args):
        raise ledger_source.LedgerUnavailable(TIMEOUT)

    monkeypatch.setattr(ledger_source, "read_ledger", broken)
    assert ledger_source.sanity_runs("/repo", "bj") == Err(TIMEOUT)

    def malformed(*_args):
        return json.loads("{not json")

    monkeypatch.setattr(ledger_source, "read_ledger", malformed)
    result = ledger_source.sanity_runs("/repo", "bj")
    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)


def _text(app: ConsoleApp, selector: str) -> str:
    return str(app.query_one(selector, Static).content)


def test_the_sanity_tab_scrolls_so_every_run_is_reachable_newest_first() -> None:
    many = _runs(60)

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT, (many, [], [])), STATE)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "sanity"
            await settle(pilot)
            scroller = app.query_one("#sanity").query_one(VerticalScroll)
            assert scroller.max_scroll_y > 0
            lines = _text(app, "#sanity-list").splitlines()
            assert len(lines) == 61  # the header and all 60 runs, uncapped
            assert "bj-59" in lines[1] and "bj-0 " in lines[-1]

    run_scenario(scenario())


def test_a_failed_or_skipped_tick_keeps_the_last_good_rows_in_the_app() -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT, ([_run(1)], [], [])), STATE)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            # A stopped tick reads no open rows: nothing to apply, nothing overwritten.
            app._finish_tick(None, "", None, None, None, None)
            assert app.open_quality_held.value == ([], []) and app.sanity_held.value is not None
            app._finish_tick(None, "", None, None, Err(TIMEOUT), Err(TIMEOUT))
            assert app.sanity_held.value is not None and app.sanity_held.error == TIMEOUT
            assert app.open_quality_held.value == ([], []) and app.open_quality_held.error == TIMEOUT
            app.refresh_views()
            text = _text(app, "#sanity-list")
            assert "bj-1" in text and "stale" in text and error_text(TIMEOUT) in text
            assert "Timeout(" not in _text(app, "#quality-error")

    run_scenario(scenario())


def test_findings_pane_says_select_a_run_before_any_is_selected() -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT), STATE)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            app._show_findings("gone")
            await settle(pilot)
            assert "Select a run" in app.query_one("#quality-findings")._markdown

    run_scenario(scenario())
