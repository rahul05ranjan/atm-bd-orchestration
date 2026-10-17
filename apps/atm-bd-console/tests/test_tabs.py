from __future__ import annotations

from dataclasses import replace

from conftest import FakeStep, run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, SPRINT_VIEW, STATE, STEP_VIEW
from rich.cells import cell_len
from textual import events

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
    assert status_header(SNAPSHOT).plain == "sprints 1/4 · \U000f05e0 1 · \U000f051f 1 · \U000f137c 2"
    assert status_header(RUNNING).plain == "sprints 0/1 · \U000f05e0 0 · \U000f051f 1 · \U000f137c 0"


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
            assert lines(app, "#status-header") == [status_header(SNAPSHOT).plain]
            assert WIDE in str(app.query_one("#status-text").render())

    run_scenario(scenario())


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
            shown = lines(app, "#sprint-steps-text")
            assert shown[0] == "atm-bh-8  renderers"
            assert [line.split()[:2] + line.split()[-1:] for line in shown[1:]] == [
                ["dev", "atm-bh-8.group-dev", "·solar"],
                ["sanity", "atm-bh-8.group-sanity", "unassigned"],
                ["qa", "atm-bh-8.group-qa", "·quality-mgr"],
                ["fix", "atm-bh-8.group-qa-f1", "·" + TEST_ASSIGNEE],
            ]
            sprints.highlighted = 0
            await settle(pilot)
            assert lines(app, "#sprint-steps-text")[1].split()[:2] == [STEP_VIEW.kind, STEP_VIEW.bead_id]

    run_scenario(scenario())


def test_pr_history_toggle_uses_the_current_snapshot_without_another_tick() -> None:
    merged = replace(RUNNING.prs[0], pr=replace(RUNNING.prs[0].pr, state="merged"))
    snapshot = replace(RUNNING, prs=(RUNNING.prs[0], merged))

    async def scenario() -> None:
        step = FakeStep(snapshot)
        app = ConsoleApp(replace(CONFIG, interval_s=3600), step, STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "prs"
            await settle(pilot)
            before = str(app.query_one("#prs-text").render()).count("\U000f062d")
            await pilot.press("m")
            await settle(pilot)
            after = str(app.query_one("#prs-text").render()).count("\U000f062d")
            assert after > before
            assert step.calls == [False, True]

    run_scenario(scenario())


def _move(widget, screen_x: int, screen_y: int) -> events.MouseMove:
    return events.MouseMove(widget, screen_x - widget.region.x, screen_y - widget.region.y, 0, 0, 1,
                            False, False, False, screen_x=screen_x, screen_y=screen_y)


def test_dag_drag_pans_the_graph_and_copies_nothing(monkeypatch) -> None:
    copied: list[str] = []
    notices: list[str] = []

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT), STATE)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        monkeypatch.setattr(app, "notify", lambda message, **_kwargs: notices.append(message))
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            tabs = app.query_one("TabbedContent")
            tabs.active = "status"
            await settle(pilot)
            panel = app.query_one("#status-panel")
            before = panel.scroll_x
            assert panel.max_scroll_x > before + 30
            x, y = panel.region.x + 20, panel.region.y + 2
            await pilot.mouse_down(panel, offset=(20, 2))
            panel.on_mouse_move(_move(panel, x - 7, y))   # drag left: the graph follows the pointer
            panel.on_mouse_move(_move(panel, x - 12, y))
            await pilot.mouse_up(panel, offset=(8, 2))
            await settle(pilot)
            assert panel.scroll_x == before + 12
            assert tabs.active == "status" and copied == [] and notices == []
            assert app.screen.get_selected_text() is None
            await pilot.click("#sprints", offset=(1, 1))
            await settle(pilot)
            assert tabs.active == "status" and copied == [] and notices == []

    run_scenario(scenario())


def test_dag_pan_follows_a_drag_that_leaves_the_panel_until_the_button_is_released(monkeypatch) -> None:
    copied: list[str] = []

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(SNAPSHOT), STATE)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "status"
            await settle(pilot)
            panel = app.query_one("#status-panel")
            before = panel.scroll_x
            await pilot.mouse_down(panel, offset=(20, 2))
            await pilot.hover("#sprints", offset=(5, 3))  # pointer is off the panel: capture keeps routing the drag
            assert app.mouse_captured is panel and panel.scroll_x != before
            panned = panel.scroll_x
            await pilot.mouse_up("#sprints", offset=(5, 3))
            assert app.mouse_captured is None
            await pilot.hover("#status-header", offset=(3, 0))
            await settle(pilot)
            assert panel.scroll_x == panned and copied == []

    run_scenario(scenario())


def test_selecting_dashboard_text_copies_it_with_a_toast(monkeypatch, tmp_path) -> None:
    copied: list[str] = []
    notices: list[str] = []

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600, state_dir=str(tmp_path / "state")), FakeStep(SNAPSHOT), STATE)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        monkeypatch.setattr(app, "notify", lambda message, **_kwargs: notices.append(message))
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            dashboard = app.query_one("#dashboard")
            await pilot.click(dashboard, offset=(1, 0))
            await settle(pilot)
            assert copied == [] and notices == []
            await pilot.mouse_down(dashboard, offset=(0, 0))
            await pilot.hover(dashboard, offset=(8, 0))
            await pilot.mouse_up(dashboard, offset=(8, 0))
            await settle(pilot)
            assert copied == [lines(app, "#dashboard")[0][:9]]
            assert notices == [f"Copied {len(copied[0])} chars"]
        trace = [line.split(" ", 1)[1] for line in app.input_log_path.read_text().splitlines()]
        assert trace.count(f"copy {len(copied[0])} chars active=dispatch") == 1

    run_scenario(scenario())


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

    run_scenario(scenario())
