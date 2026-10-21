from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE, TIMEOUT
from textual.widgets import OptionList

from atm_bd_console.app import ConsoleApp
from atm_bd_console.poll_health import PollHealth
from atm_bd_console.result import IoFailed
from atm_bd_console.timefmt import clock
from atm_bd_console.types import Metrics, StepResult

LATER = RUNNING.generated_at + timedelta(seconds=60)
LATEST = RUNNING.generated_at + timedelta(seconds=120)


def blanked(**fields) -> object:
    """What model.tick builds when the phase read failed: every phase-derived part empty, the error listed."""
    fields = {"generated_at": LATER, **fields}
    return replace(
        RUNNING, phase="", phase_data=None, sprints=(), sections=(), agents=(), problems=(),
        prs=(), top_pr=None, top_ci=None, metrics=Metrics(0, 0, 0, 0), mermaid="graph TD", errors=(*RUNNING.errors, TIMEOUT),
        member_data=(), task_data=(), task_bead_data=(), worktree_data=(), **fields,
    )


def test_phase_failure_keeps_every_phase_part_and_shows_one_indicator() -> None:
    health = PollHealth()
    assert health.apply(RUNNING, {}) is RUNNING
    shown = health.apply(blanked(), {"beads.open": TIMEOUT})
    for part in ("phase_data", "sprints", "sections", "agents", "problems", "prs", "metrics", "mermaid", "phase", "top_pr"):
        assert getattr(shown, part) == getattr(RUNNING, part), part
    assert shown.generated_at == LATER
    assert TIMEOUT in shown.errors  # kept, tagged `carried since T` by the renderers
    assert health.carried_since(TIMEOUT) == LATER
    assert health.phase_error() == TIMEOUT
    assert health.indicator(LATER) == (
        f"! beads.open failed since {clock(LATER, seconds=True)} · showing data from {clock(RUNNING.generated_at, seconds=True)} (1m old)")


def test_failure_since_is_kept_while_it_persists_and_clears_on_success() -> None:
    health = PollHealth()
    health.apply(RUNNING, {})
    health.apply(blanked(), {"beads.open": TIMEOUT})
    health.apply(replace(blanked(), generated_at=LATEST), {"beads.open": TIMEOUT})
    assert f"since {clock(LATER, seconds=True)}" in (health.indicator(LATEST) or "")
    recovered = replace(RUNNING, generated_at=LATEST + timedelta(seconds=60))
    assert health.apply(recovered, {}) is recovered
    assert health.indicator(LATEST) is None


@pytest.mark.parametrize("source", ["atm.members", "atm.tasks", "beads.show_tasks", "beads.task_ancestors"])
def test_agent_source_failure_keeps_agents_and_held_but_takes_fresh_phase(source) -> None:
    health = PollHealth()
    health.apply(RUNNING, {})
    fresh_phase = replace(RUNNING, generated_at=LATER, agents=(), member_data=(), task_data=(), task_bead_data=(),
                          sections=(), problems=(), metrics=Metrics(9, 9, 100, 1), errors=(TIMEOUT,))
    shown = health.apply(fresh_phase, {source: TIMEOUT})
    assert shown.agents == RUNNING.agents and shown.member_data == RUNNING.member_data
    assert shown.task_data == RUNNING.task_data and shown.sections == RUNNING.sections
    assert shown.problems == RUNNING.problems
    assert shown.metrics == Metrics(9, 9, 100, 1)
    assert shown.errors == (TIMEOUT,)
    assert source in (health.indicator(LATEST) or "")


@pytest.mark.parametrize("source", ["git.worktrees", "atm.task_events"])
def test_dispatch_input_failure_keeps_held_rows_and_problems(source) -> None:
    health = PollHealth()
    health.apply(RUNNING, {})
    fresh = replace(RUNNING, generated_at=LATER, sections=(), problems=(), worktree_data=(), errors=(TIMEOUT,))
    shown = health.apply(fresh, {source: TIMEOUT})
    assert shown.sections == RUNNING.sections and shown.problems == RUNNING.problems
    assert shown.worktree_data == (RUNNING.worktree_data if source == "git.worktrees" else ())
    assert shown.agents == RUNNING.agents


def test_failure_with_nothing_good_yet_shows_the_snapshot_unchanged() -> None:
    health = PollHealth()
    cold = blanked()
    assert health.apply(cold, {"beads.open": TIMEOUT}) == cold


def test_unrelated_source_failure_changes_nothing() -> None:
    health = PollHealth()
    health.apply(RUNNING, {})
    fresh = replace(RUNNING, generated_at=LATER, errors=(TIMEOUT,))
    assert health.apply(fresh, {"git.fetch": TIMEOUT}) == fresh
    assert health.indicator(LATEST) is None


def test_tick_failure_leaves_data_and_clears_on_next_success() -> None:
    health = PollHealth()
    health.apply(RUNNING, {})
    health.tick_failed(IoFailed("io_failed", "console tick", "boom", "retry"), LATER)
    health.tick_failed(IoFailed("io_failed", "console tick", "boom", "retry"), LATEST)
    assert health.last_good is RUNNING
    assert health.indicator(LATER) == (
        f"! tick failed since {clock(LATER, seconds=True)} · showing data from {clock(RUNNING.generated_at, seconds=True)} (1m old)")
    health.apply(replace(RUNNING, generated_at=LATEST), {})
    assert health.indicator(LATEST) is None


class ScriptedStep:
    """Returns each scripted result in turn; an Exception instance is raised instead."""

    def __init__(self, *script) -> None:
        self.script = list(script)

    def __call__(self, _config, state, _force) -> StepResult:
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def _screens(app: ConsoleApp) -> dict[str, str]:
    options = app.query_one("#sprints", OptionList)
    return {
        "sprints": str(options.option_count),
        "table": str(app.query_one("#status-table").display),
        "dashboard": str(app.query_one("#dashboard").render()),
        "prs": str(app.query_one("#prs-text").render()),
        "status-bar": str(app.query_one("#status-bar").render()),
        "header": str(app.query_one("#status-header").render()),
    }


def _drive(script) -> tuple[dict, dict, dict]:
    seen: list[dict] = []

    async def scenario() -> None:
        app = ConsoleApp(CONFIG, ScriptedStep(*script), STATE)
        async with app.run_test() as pilot:
            await settle(pilot)
            seen.append(_screens(app))
            app.action_refresh()
            await settle(pilot)
            seen.append(_screens(app))
            app.action_refresh()
            await settle(pilot)
            seen.append(_screens(app))

    run_scenario(scenario())
    return seen[0], seen[1], seen[2]


@pytest.mark.parametrize("failure", [
    StepResult(blanked(generated_at=RUNNING.generated_at), STATE, failed_reads={"beads.open": TIMEOUT}),
    StepResult(blanked(generated_at=RUNNING.generated_at), STATE, failed_reads={"phase_file": TIMEOUT}),
    RuntimeError("tick exploded"),
])
def test_every_screen_keeps_its_data_through_a_failed_poll_and_recovers(failure) -> None:
    good = StepResult(RUNNING, STATE)
    before, during, after = _drive((good, failure, good))
    for screen in ("sprints", "table", "prs", "status-bar"):
        assert during[screen] == before[screen], screen
    # The dashboard keeps every line it had and adds the stale indicator, which says how old the content is.
    assert set(before["dashboard"].splitlines()) <= set(during["dashboard"].splitlines()) | {
        line for line in before["dashboard"].splitlines() if line.startswith("== Errors")}
    assert "failed since" in during["dashboard"] and "showing data from" in during["dashboard"]
    assert "failed since" not in before["dashboard"] and "failed since" not in after["dashboard"]
    assert "failed since" in during["header"]
    assert "failed since" not in before["header"] and "failed since" not in after["header"]
    assert after == before
