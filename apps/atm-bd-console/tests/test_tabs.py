from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import FakeStep, run_scenario, settle
from contract_fixtures import RUNNING
from contract_samples import CONFIG, PHASE, SPRINT_VIEW, STATE, STEP_VIEW
from rich.cells import cell_len
from textual import events
from textual.widgets._markdown import MarkdownBlock

from atm_bd_console import runs
from atm_bd_console.app import ConsoleApp, _agent_name, _run_time, sanity_text, sprint_steps, status_header
from atm_bd_console.enums import PrState, SprintState
from atm_bd_console.icons import icon_for
from atm_bd_console.model import _sprint_views
from atm_bd_console.prs import render_prs
from atm_bd_console.render import dashboard, status_bar
from atm_bd_console.status import _fallback
from atm_bd_console.types import AgentView, SprintView, StepView

WIDE = "x" * 200
TEST_ASSIGNEE = "test-dev"
SPRINTS = (
    SPRINT_VIEW,
    SprintView("atm-bh-1", "contract", SprintState.CLOSED, (), ()),
    SprintView("atm-bh-9", "runner", SprintState.NOT_READY, ("atm-bh-6",), ()),
    SprintView("atm-bh-8", "renderers", SprintState.READY, (), (
        StepView("atm-bh-8.group-dev", "dev", SprintState.IN_PROGRESS, "solar"),
        StepView("atm-bh-8.group-sanity", "sanity", SprintState.OPEN, None),
        StepView("atm-bh-8.group-qa", "qa", SprintState.OPEN, "quality-mgr"),
        StepView("atm-bh-8.group-qa-f1", "fix", SprintState.OPEN, TEST_ASSIGNEE),
    )),
)
SNAPSHOT = replace(
    RUNNING,
    agents=(replace(RUNNING.agents[0], name=TEST_ASSIGNEE),),
    sprints=SPRINTS,
    mermaid=f"graph TD\n  {WIDE}",
)


def capture_updates(monkeypatch, widget):
    pending = []
    original = widget.update

    def update(content):
        result = original(content)
        pending.append(result)
        return result

    monkeypatch.setattr(widget, "update", update)
    return pending


def test_sanity_rows_show_local_time_icon_name_and_api_failure() -> None:
    dev = replace(RUNNING.agents[0], name="alice", agent_type="dev", harness="codex-cli", model="terra")
    reviewer = replace(RUNNING.agents[0], name="sanity-jev", agent_type="sanity", harness="codex-cli", model="sol")
    rows = [{"run_id": "r", "completed_at": "2026-10-02T13:14:00Z", "sprint": "bj-4", "pr_number": 42,
             "iteration": 1, "final_verdict": "PASS", "dev": "alice", "reviewer": "sanity-jev",
             "kind": "API_FAILURE", "jev_elapsed_s": 4.2, "error": {"message": "Jev is unavailable"}},
            {"run_id": "r", "completed_at": "2026-10-02T13:14:00Z", "sprint": "bj-4", "reviewer": "sanity-llm",
             "verdict": "PASS", "llm_elapsed_s": 1.2}]
    main, errors = sanity_text(runs.sanity_runs(rows), (dev, reviewer))
    assert _run_time("2026-10-02T13:14:00Z") in main.plain
    assert "\U000f01e7 alice" in main.plain
    assert "\U000f0026" in main.plain and "4.2s" in main.plain and "1.2s" in main.plain
    assert "\U000f01a6 sanity-jev" in errors.plain and "Jev is unavailable" in errors.plain


def test_sanity_and_quality_panes_show_fixture_data(monkeypatch, tmp_path) -> None:
    qa = {"id": "qa-1", "status": "closed", "closed_at": "2026-10-02T13:14:00Z", "close_reason": "FAIL found",
          "metadata": {"sprint": "bj-4", "round": 1}, "checked_bead_data": {"metadata": {"pr_number": 42}}}
    finding = {"id": "f1", "description": "missing test", "design": "add a test",
               "metadata": {"severity": "important", "reviewer": "req-qa"},
               "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-1"}]}
    monkeypatch.setattr(runs, "collect", lambda *_args: ([], runs.qa_runs([qa]), [finding]))
    rendered = []
    monkeypatch.setattr(runs, "render_findings", lambda _root, findings: rendered.extend(findings) or "# Findings\n\nmissing test")

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600, state_dir=str(tmp_path)), FakeStep(SNAPSHOT), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            tabs = app.query_one("TabbedContent")
            tabs.active = "sanity"
            assert app.query_one("#sanity-list") and app.query_one("#sanity-errors")
            tabs.active = "quality"
            options = app.query_one("#quality-runs")
            assert options.option_count == 1
            label = str(options.get_option_at_index(0).prompt)
            assert _run_time(qa["closed_at"]) in label and "bj-4" in label and "1/0" in label and "FAIL" in label
            widget = app.query_one("#quality-findings")
            pending = capture_updates(monkeypatch, widget)
            app._show_findings("qa-1")
            await pending[-1]
            assert rendered and rendered[-1]["id"] == "f1"
            assert widget

    run_scenario(scenario())


@pytest.mark.parametrize(("status", "reason", "color"), [
    ("open", None, "#4A5AF8"), ("closed", "PASS", "#2EA043"), ("closed", "FAIL", "#F85149"),
])
def test_quality_finding_reviewer_retains_roster_icon_color(monkeypatch, tmp_path, status, reason, color) -> None:
    qa = {"id": "qa-1", "status": status, "closed_at": "2026-10-02T13:14:00Z", "close_reason": reason,
          "metadata": {"sprint": "bj-4", "round": 1}}
    finding = {"id": "f1", "description": "issue", "design": "correction",
               "metadata": {"severity": "important", "reviewer": "req-qa"},
               "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-1"}]}
    reviewer = replace(RUNNING.agents[0], name="req-qa", agent_type="qa")
    snapshot = replace(SNAPSHOT, agents=(reviewer,))
    monkeypatch.setattr(runs, "collect", lambda *_args: ([], runs.qa_runs([qa]), [finding]))
    monkeypatch.setattr(runs, "render_findings", lambda _root, findings: f"**Reviewer:** {findings[0]['reviewer']}")

    async def scenario() -> None:
        app = ConsoleApp(replace(CONFIG, interval_s=3600, state_dir=str(tmp_path)), FakeStep(snapshot), STATE)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("TabbedContent").active = "quality"
            widget = app.query_one("#quality-findings")
            pending = capture_updates(monkeypatch, widget)
            app._show_findings("qa-1")
            await pending[-1]
            blocks = widget.query(MarkdownBlock)
            content = next(block._content for block in blocks if "req-qa" in block._content.plain)
            assert "\U000f029b req-qa" in content.plain
            assert any(str(span.style) == color for span in content.spans)

    run_scenario(scenario())


@pytest.mark.parametrize(("harness", "model", "expected"), [
    ("claude-code", "unknown", "\U000f0382"), ("codex-cli", "unknown", "\U000f01e7"),
    ("claude-code", "HAIKU", "\U000f0156"), ("codex-cli", "SOL", "\U000f05a8"),
])
def test_run_dev_cell_uses_roster_model_and_fallback_icon(harness, model, expected) -> None:
    dev = replace(RUNNING.agents[0], name="alice", agent_type="dev", harness=harness, model=model)
    label = _agent_name("alice", (dev,))
    assert label.plain == f"{expected} alice"
    assert (expected, label.style) == icon_for(dev)


def lines(app: ConsoleApp, selector: str) -> list[str]:
    return str(app.query_one(selector).render()).splitlines()


def test_status_header_counts_the_fixture_sprints() -> None:
    assert status_header(SNAPSHOT).plain == "sprints 1/4 · \U000f05e0 1 · \U000f051f 1 · \U000f137c 2"
    assert status_header(RUNNING).plain == "sprints 0/1 · \U000f05e0 0 · \U000f051f 1 · \U000f137c 0"


def test_anc_023_same_fixture_renders_agent_icon_before_name_on_every_surface(tmp_path) -> None:
    name = "terra-agent"
    icon_and_name = "\U000f01e7" + name
    agent = AgentView(name, "busy", RUNNING.generated_at, 1, RUNNING.agents[0].rows,
                      "dev", "GPT-5.6-Terra", "codex-cli")
    phase = replace(PHASE, steps=(replace(PHASE.steps[0], assignee=name),))
    sprints, mermaid = _sprint_views(phase, "atm", (agent,))
    sprint = sprints[0]
    snapshot = replace(
        RUNNING,
        agents=(agent,),
        lead=name,
        sprints=sprints,
        mermaid=mermaid,
        sent=(replace(RUNNING.sent[0], to=name),),
        prs=(replace(RUNNING.prs[0], assignee=name),),
    )

    # Dashboard roster heading.
    dashboard_text = dashboard(snapshot, 160, 40).plain
    assert icon_and_name in dashboard_text
    # Dashboard's sent-recipient row.
    assert f"→ {icon_and_name}" in dashboard_text
    # Sprint row in the plain text DAG fallback.
    assert icon_and_name in _fallback(snapshot)
    # Task row in sprint details.
    assert icon_and_name in sprint_steps(sprint, snapshot.agents).plain
    # Status bar lead.
    assert icon_and_name in status_bar(snapshot, 160).plain
    # Mermaid DAG sprint node.
    assert icon_and_name in snapshot.mermaid
    # PR list assignee.
    assert icon_and_name in render_prs(snapshot, 160).plain

    async def scenario() -> None:
        app = ConsoleApp(
            replace(CONFIG, interval_s=3600, state_dir=str(tmp_path / "state")),
            FakeStep(snapshot), STATE,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            # Diagnostics agent line.
            app.query_one("TabbedContent").active = "diagnostics"
            await settle(pilot)
            diagnostics = app.query_one("#diagnostics-text").render().plain
            assert icon_and_name in diagnostics

    run_scenario(scenario())


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
    merged = replace(RUNNING.prs[0], pr=replace(RUNNING.prs[0].pr, state=PrState.MERGED))
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
