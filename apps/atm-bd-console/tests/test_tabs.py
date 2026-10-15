from __future__ import annotations

import asyncio
from dataclasses import replace

from conftest import FakeStep, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, SPRINT_VIEW, STATE, STEP_VIEW
from rich.cells import cell_len

from atm_bd_console.app import ConsoleApp, status_header
from atm_bd_console.types import SprintView, StepView

WIDE = "x" * 200
TEST_ASSIGNEE = "test-dev"
SPRINTS = (
    SPRINT_VIEW,
    SprintView("atm-bh-1", "contract", "closed", (), ()),
    SprintView("atm-bh-9", "runner", "not_ready", ("atm-bh-6",), ()),
    SprintView("atm-bh-8", "renderers", "ready", (), (
        StepView("atm-bh-8.group-dev", "dev", "in_progress", "solar"),
        StepView("atm-bh-8.group-sanity", "sanity", "open", None),
        StepView("atm-bh-8.group-qa", "qa", "open", "quality-mgr"),
        StepView("atm-bh-8.group-qa-f1", "fix", "open", TEST_ASSIGNEE),
    )),
)
SNAPSHOT = replace(
    RUNNING,
    agents=(replace(RUNNING.agents[0], name=TEST_ASSIGNEE),),
    sprints=SPRINTS,
    mermaid=f"graph TD\n  {WIDE}",
)


def lines(app: ConsoleApp, selector: str) -> list[str]:
    return str(app.query_one(selector).render()).splitlines()


def test_status_header_counts_the_fixture_sprints() -> None:
    assert status_header(SNAPSHOT) == "sprints 1/4 · ✅ 1 · 🌀 1 · ⏳ 2"
    assert status_header(RUNNING) == "sprints 0/1 · ✅ 0 · 🌀 1 · ⏳ 0"


def test_three_tabs_render_the_running_snapshot_with_the_status_bar() -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            tabs = app.query_one("TabbedContent")
            bar = app.query_one("#status-bar")
            for pane in ("dispatch", "status", "prs"):
                tabs.active = pane
                await settle(pilot)
                assert bar.display and bar.region.height == 1 and bar.region.y == 39
                assert "tick " in str(bar.render())
            assert lines(app, "#dashboard")[0].startswith("phase-bh")
            assert TEST_ASSIGNEE in str(app.query_one("#dashboard").render())
            assert "#1850" in str(app.query_one("#prs-text").render())
            assert lines(app, "#status-header") == ["sprints 1/4 · ✅ 1 · 🌀 1 · ⏳ 2"]
            assert WIDE in str(app.query_one("#status-text").render())

    asyncio.run(scenario())


def test_selecting_a_sprint_lists_its_steps_with_assignees() -> None:
    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            sprints = app.query_one("#sprints")
            sprints.focus()
            sprints.highlighted = 3
            await pilot.press("enter")
            await settle(pilot)
            shown = lines(app, "#sprint-steps")
            assert shown[0] == "atm-bh-8  renderers"
            assert [line.split()[:2] + line.split()[-1:] for line in shown[1:]] == [
                ["dev", "atm-bh-8.group-dev", "solar"],
                ["sanity", "atm-bh-8.group-sanity", "unassigned"],
                ["qa", "atm-bh-8.group-qa", "quality-mgr"],
                ["fix", "atm-bh-8.group-qa-f1", TEST_ASSIGNEE],
            ]
            sprints.highlighted = 0
            await settle(pilot)
            assert lines(app, "#sprint-steps")[1].split()[:2] == [STEP_VIEW.kind, STEP_VIEW.bead_id]

    asyncio.run(scenario())


def test_resizing_to_80_columns_rerenders_without_wide_lines() -> None:
    long_row = replace(RUNNING.agents[0].rows[0], description="d" * 300)
    snapshot = replace(SNAPSHOT, agents=(replace(RUNNING.agents[0], rows=(long_row,)),))

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(snapshot), STATE)
        async with app.run_test(size=(160, 40)) as pilot:
            await settle(pilot)
            assert max(cell_len(line) for line in lines(app, "#dashboard")) > 80
            await pilot.resize_terminal(80, 30)
            await settle(pilot)
            for selector in ("#dashboard", "#prs-text", "#status-bar", "#status-header"):
                assert max(cell_len(line) for line in lines(app, selector)) <= 80, selector
            # The status diagram scrolls sideways instead of wrapping.
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            assert WIDE in str(app.query_one("#status-text").render())
            assert app.query_one("#status-text").size.height == 2
            assert app.query_one("#status-panel").max_scroll_x > 0

    asyncio.run(scenario())
