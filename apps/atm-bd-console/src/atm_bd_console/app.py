"""The interactive console: one Textual app over an injected step function."""
from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import ClassVar, Protocol

from markdown_it.token import Token
from rich.cells import cell_len
from rich.text import Text
from sc_observability import Level
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, ScrollableContainer, VerticalScroll
from textual.css.query import NoMatches
from textual.errors import NoWidget
from textual.message import Message
from textual.widgets import Markdown, OptionList, Static, TabbedContent, TabPane
from textual.widgets._markdown import MarkdownBlock
from textual.widgets._tabbed_content import ContentTab
from textual.widgets.option_list import Option

from . import findings_renderer, ledger_source, prs, render, runs, status, status_table
from .agent_roles import agent_group
from .build import display_identity
from .enums import QaState, SprintState
from .icons import JEV_VERDICT_ICONS, LLM_VERDICT_ICONS, SPRINT_ICONS, icon_for, qa_run_icon
from .log import create_console_logger, error_message
from .result import Err, Error, IoFailed, Ok
from .state import StateStore, acquire_lock
from .terminal import CONSOLE_DRIVER_CLASS
from .types import INITIAL_STATE, AgentView, Config, Snapshot, SprintView, State, StepFn, StepResult

STEP_KINDS = ("dev", "sanity", "qa", "fix")


class FindingsMarkdown(Markdown):
    """Render the strict Markdown document while retaining roster icon colours."""

    reviewer_colors: dict[str, str]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.reviewer_colors = {}

    def _parse_markdown(self, tokens: Iterable[Token]) -> Iterable[MarkdownBlock]:
        def color(block: MarkdownBlock) -> None:
            content = block._content
            source = (block.source or "").lstrip()
            prefix = "Reviewer: "
            if source.startswith("**Reviewer:**") and content.plain.startswith(prefix):
                display = content.plain[len(prefix):]
                style = self.reviewer_colors.get(display)
                if style:
                    content = content.stylize(style, len(prefix), len(content.plain))
            block.set_content(content)
            for child in block._blocks:
                color(child)

        for block in super()._parse_markdown(tokens):
            color(block)
            yield block


class _DiagnosticEvent(Protocol):
    timestamp: str
    level: Level
    message: str | None


def status_header(snapshot: Snapshot) -> Text:
    """Show sprint counts using the same status icons as the DAG."""
    states = [sprint.state for sprint in snapshot.sprints]
    closed, running = states.count(SprintState.CLOSED), states.count(SprintState.IN_PROGRESS)
    waiting = len(states) - closed - running
    result = Text(f"sprints {closed}/{len(states)} · ")
    for index, (state, count) in enumerate(((SprintState.CLOSED, closed), (SprintState.IN_PROGRESS, running), (SprintState.NOT_READY, waiting))):
        if index:
            result.append(" · ")
        glyph, style = SPRINT_ICONS[state]
        result.append(glyph, style=style)
        result.append(f" {count}")
    return result


def _diagnostic_line(event: _DiagnosticEvent) -> str:
    """Add local display time and level to a stored message without interpreting it."""
    return f"{render.clock(datetime.fromisoformat(event.timestamp), seconds=True)} {event.level}: {event.message or ''}"


def sprint_steps(sprint: SprintView, agents: tuple[AgentView, ...] = ()) -> Text:
    lines = [Text(f"{sprint.id}  {sprint.title}", no_wrap=True)]
    for step in sprint.steps:
        if step.kind in STEP_KINDS:
            line = Text(f"  {step.kind:<6} {step.bead_id}  {step.status}  ")
            member = next((agent for agent in agents if agent.name == step.assignee), None)
            line.append_text(render.render_agent(step.assignee, member))
            line.no_wrap = True
            lines.append(line)
    return Text("\n").join(lines)


def _run_time(value: str, *, local_zone: tzinfo | None = None) -> str:
    try:
        return render.clock(datetime.fromisoformat(value), local_zone=local_zone)
    except ValueError:
        return "—"


def _verdict(row: dict | None, reviewer: str) -> Text:
    if not row:
        return Text("—")
    kind = "API_FAILURE" if row.get("kind") == "API_FAILURE" else row.get("verdict")
    icons = JEV_VERDICT_ICONS if reviewer == "sanity-jev" else LLM_VERDICT_ICONS
    glyph, color = icons.get(str(kind), ("?", None))
    label = {"PASS": "pass", "FAIL": "fail"}.get(str(kind), "")
    return Text(f"{glyph}{f' {label}' if label else ''}", style=color or "")


def _duration(value: object) -> str:
    return f"{value:g}s" if type(value) in (int, float) else "—"  # type: ignore[operator]


def _agent_name(name: str | None, agents: tuple[AgentView, ...]) -> Text:
    if not name:
        return Text("—")
    member = next((agent for agent in agents if agent.name == name), None)
    glyph, color = icon_for(member)
    result = Text(glyph, style=color or "")
    result.append(f" {name}")
    return result


def _aligned(rows: list[list[Text]]) -> Text:
    widths = [max(cell_len(row[index].plain) for row in rows) for index in range(len(rows[0]))]
    rendered = []
    for row in rows:
        line = Text()
        for index, cell in enumerate(row):
            line.append_text(cell)
            if index < len(row) - 1:
                line.append(" " * (widths[index] - cell_len(cell.plain) + 2))
        rendered.append(line)
    return Text("\n").join(rendered)


def sanity_text(
    items: list[runs.SanityRun], agents: tuple[AgentView, ...], *, local_zone: tzinfo | None = None,
) -> tuple[Text, Text]:
    lines = [[Text(value) for value in ("Completed", "Sprint", "PR", "Dev", "JEV", "Elapsed", "LLM", "Elapsed", "Final", "Iter")]]
    errors = [[Text(value) for value in ("Sprint", "Iter", "Reviewer", "Error")]]
    for run in items:
        line = [Text(_run_time(run.completed_at, local_zone=local_zone)), Text(run.sprint), Text(f"#{run.pr_number or '—'}"), _agent_name(run.dev, agents)]
        for row, reviewer, field in ((run.jev, "sanity-jev", "jev_elapsed_s"), (run.llm, "sanity-llm", "llm_elapsed_s")):
            line.extend((_verdict(row, reviewer), Text(_duration(row.get(field) if row else None))))
            if row and row.get("kind") == "API_FAILURE":
                name = str(row.get("reviewer") or "")
                error = [Text(run.sprint), Text(str(run.iteration or "—")), _agent_name(name, agents), _verdict(row, name)]
                detail = row.get("error")
                rendered = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                error.append(Text(rendered))
                errors.append(error)
        line.extend((Text(run.final_verdict or "—"), Text(str(run.iteration or "—"))))
        lines.append(line)
    return _aligned(lines), _aligned(errors)


_QUALITY_HEADERS = ("Completed", "QA", "Sprint", "QA bead", "PR", "Round", "B", "I", "M", "Elapsed")
_QUALITY_QA_COLUMN = 1
_QUALITY_SPRINT_COLUMN = 2
_QUALITY_BLOCKING_COLUMN = 6
_QUALITY_IMPORTANT_COLUMN = 7
_QUALITY_MINOR_COLUMN = 8
_QUALITY_SEVERITY_COLUMNS = (_QUALITY_BLOCKING_COLUMN, _QUALITY_IMPORTANT_COLUMN, _QUALITY_MINOR_COLUMN)


def _quality_run_cells(
    run: runs.QaRun, findings: list[dict], *, local_zone: tzinfo | None = None,
) -> tuple[tuple[str, ...], QaState | None, int]:
    counts = [sum(f["severity"] == severity for f in findings) for severity in ("blocking", "important", "minor")]
    state = run.verdict
    qa_status = state.name.lower() if state is not None and state != QaState.RUNNING else ""
    cells = (
        _run_time(run.completed_at, local_zone=local_zone),
        qa_status,
        run.sprint,
        str(run.bead.get("id", "")),
        f"#{run.pr_number or '—'}",
        str(run.round or "—"),
        str(counts[0]),
        str(counts[1]),
        str(counts[2]),
        _duration(run.elapsed_s),
    )
    return cells, run.verdict, counts[0]


def _quality_widths(rows: list[tuple[tuple[str, ...], QaState | None, int]]) -> tuple[int, ...]:
    def width(row: tuple[str, ...], state: QaState | None, blocking: int, index: int) -> int:
        rendered = cell_len(row[index])
        icon = qa_run_icon(state, blocking)
        if index == _QUALITY_QA_COLUMN and icon is not None:
            rendered += cell_len(icon[0]) + 1
        return rendered

    return tuple(max([cell_len(header), *(width(row, state, blocking, index) for row, state, blocking in rows)])
                 for index, header in enumerate(_QUALITY_HEADERS))


def _quality_row(cells: tuple[str, ...], state: QaState | None, blocking: int, widths: tuple[int, ...]) -> Text:
    label = Text(no_wrap=True)
    for index, (value, width) in enumerate(zip(cells, widths, strict=True)):
        icon = qa_run_icon(state, blocking)
        if index == _QUALITY_QA_COLUMN and icon is not None:
            glyph, color = icon
            label.append(glyph, style=color or "")
            label.append(f" {value}".ljust(max(0, width - cell_len(glyph))))
        elif index in _QUALITY_SEVERITY_COLUMNS and value == "0":
            label.append(" " * width)
        else:
            rendered = value.rjust(width) if index in _QUALITY_SEVERITY_COLUMNS else value.ljust(width)
            label.append(rendered)
        if index < len(cells) - 1:
            label.append(" | ")
    return label


def _quality_header(widths: tuple[int, ...]) -> Text:
    header = Text(no_wrap=True, style="bold")
    for index, (value, width) in enumerate(zip(_QUALITY_HEADERS, widths, strict=True)):
        header.append(value.ljust(width))
        if index < len(_QUALITY_HEADERS) - 1:
            header.append(" | ")
    return header


def _lock_message(error: Error) -> str:
    if error.kind == "lock_held":
        age = "no snapshot" if error.snapshot_age_s is None else f"snapshot age {error.snapshot_age_s}s"
        return f"atm-bd console: lock held by a running console ({age}); {error.fix}"
    return f"atm-bd console: {error.kind}: {getattr(error, 'path', '')}: {getattr(error, 'message', '')}; {error.fix}"


class GraphText(Static):
    """The DAG: never text-selectable, its drag pans (#252)."""

    ALLOW_SELECT = False


class StatusPanel(ScrollableContainer):
    """Scrolls the DAG; a mouse drag pans it by the drag delta (#252), keyboard ←/→ pan as before."""

    ALLOW_SELECT = False

    class Resized(Message):
        pass

    def on_resize(self) -> None:
        self.post_message(self.Resized())

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._pan_origin = (event.screen_x, event.screen_y, self.scroll_x, self.scroll_y)
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        origin = getattr(self, "_pan_origin", None)
        if origin is not None:
            start_x, start_y, scroll_x, scroll_y = origin
            self.scroll_to(x=scroll_x - (event.screen_x - start_x), y=scroll_y - (event.screen_y - start_y),
                           animate=False, immediate=True)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._pan_origin = None
        self.release_mouse()
        event.stop()


class SprintList(OptionList):
    async def _on_click(self, event: events.Click) -> None:
        await super()._on_click(event)
        event.stop()


class ConsoleApp(App[int]):
    CSS = """
    TabbedContent, TabPane { height: 1fr; }
    #dispatch-panel { height: 1fr; }
    #status-panel { overflow: auto auto; height: 1fr; }
    #status-text { width: auto; height: auto; }
    #status-overflow { display: none; height: 1; }
    #sprint-details { height: 11; }
    #sprints { width: 1fr; height: 1fr; }
    #sprint-steps { width: 1fr; height: 1fr; }
    #sprint-steps-text { width: auto; height: auto; }
    #status-bar { dock: bottom; height: 1; }
    Screen { layers: base overlay; }
    #build { layer: overlay; position: absolute; width: auto; height: 1; }
    #diagnostics { height: 1fr; overflow: auto; }
    #sanity-list { height: 3fr; overflow: auto; }
    #sanity-errors { height: 2fr; overflow: auto; }
    #quality-runs { height: 3fr; }
    #quality-runs-header { height: 1; margin-left: 2; }
    #quality-findings { height: 2fr; overflow: auto; }
    """
    # Only q quits (ANC-014): Textual's default ctrl+q quit binding is replaced.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("m", "toggle_pr_history", "PR history"),
        Binding("d", "show_diagnostics", "Diagnostics"),
        Binding("ctrl+q", "noop", show=False, priority=True),
    ]

    def __init__(self, config: Config, step: StepFn, state: State, startup_error: Error | None = None) -> None:
        super().__init__(driver_class=CONSOLE_DRIVER_CLASS)
        self.config = config
        self.step = step
        self.state = state
        self.startup_error = startup_error
        self.snapshot: Snapshot | None = None
        self.status_text = ""
        self.status_error: Error | None = None
        self.tick_error: Error | None = None
        self._step_lock = threading.Lock()
        self._tick_running = False
        self._pending_forced_refresh = False
        self._status_view_initialized = False
        self.show_pr_history = False
        self.sanity_runs: list[runs.SanityRun] = []
        self.quality_runs: list[runs.QaRun] = []
        self.finding_beads: list[dict] = []
        self.runs_error: str | None = None
        self._open_quality_runs: list[runs.QaRun] = []
        self._open_findings: list[dict] = []
        self._closed_quality_runs: list[runs.QaRun] = []
        self._closed_findings: list[dict] = []
        self._quality_error: str | None = None
        # Every mouse press, click, key and tab change with the widget it landed on (#241 evidence).
        self.input_log_path = Path(config.state_dir) / "input.log"

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="dispatch"):
            with TabPane("Dispatch", id="dispatch"), VerticalScroll(id="dispatch-panel"):
                yield Static(id="dashboard")
                yield Static(id="status-table")
            with TabPane("Status", id="status"):
                yield Static(id="status-header")
                yield Static("↔ Wide graph · focus graph and use ←/→ to pan", id="status-overflow")
                with StatusPanel(id="status-panel"):
                    yield GraphText(id="status-text")
                with Horizontal(id="sprint-details"):
                    yield SprintList(id="sprints")
                    with VerticalScroll(id="sprint-steps"):
                        yield Static(id="sprint-steps-text")
            with TabPane("PRs", id="prs"), VerticalScroll():
                yield Static(id="prs-text")
            with TabPane("Sanity", id="sanity"):
                yield Static(id="sanity-list")
                yield Static(id="sanity-errors")
            with TabPane("Quality", id="quality"):
                yield Static(id="quality-error")
                yield Static(id="quality-runs-header")
                yield OptionList(id="quality-runs")
                yield FindingsMarkdown(id="quality-findings")
            with TabPane("Diagnostics", id="diagnostics"):
                yield Static(id="diagnostics-text")
        yield Static(id="status-bar")
        # On the tab-header row, right-justified (#254): an overlay, so the tabs and content keep every row.
        yield Static(self.build_line, id="build")

    @property
    def build_line(self) -> str:
        return f"atm-bd console {display_identity()}"

    def _place_build_line(self) -> None:
        """Right-justify the build line on the tab-header row; hide it when it would cover a tab label."""
        build = self.query_one("#build", Static)
        strip_right = max((tab.region.right for tab in self.query(ContentTab)), default=0)
        text_width = cell_len(self.build_line)
        build.display = strip_right + 1 + text_width <= self.size.width
        build.offset = (max(self.size.width - text_width, 0), 0)

    def _on_interval(self) -> None:
        # A stopped snapshot ends ticking until r.
        if self.snapshot is None or self.snapshot.stopped is None:
            self.tick(False)

    def action_refresh(self) -> None:
        self.tick(True)
        if self.query_one(TabbedContent).active == "quality":
            self._load_closed_quality()

    def action_noop(self) -> None:
        return None

    def action_toggle_pr_history(self) -> None:
        self.show_pr_history = not self.show_pr_history
        self.refresh_views()

    def action_show_diagnostics(self) -> None:
        self.query_one(TabbedContent).active = "diagnostics"

    def on_text_selected(self) -> None:
        """A finished text selection outside the DAG is copied with a toast (#253)."""
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify(f"Copied {len(text)} chars", timeout=2)
            self._trace_input(f"copy {len(text)} chars active={self.query_one(TabbedContent).active}")

    def on_mount(self) -> None:
        self._trace_input(f"start size={self.size.width}x{self.size.height} driver={type(self._driver).__name__} "
                          f"TERM={os.environ.get('TERM', '')} TERM_PROGRAM={os.environ.get('TERM_PROGRAM', '')} "
                          f"TMUX={'yes' if os.environ.get('TMUX') else 'no'}")
        self.call_after_refresh(self._place_build_line)  # tab headers have a region only after the first layout
        self.tick(False)
        self.set_interval(self.config.interval_s, self._on_interval)

    def _trace_input(self, entry: str) -> None:
        try:
            self.input_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.input_log_path.open("a", encoding="utf-8") as log_file:
                timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                log_file.write(f"{timestamp} {entry}\n")
        except OSError:
            pass

    async def on_event(self, event: events.Event) -> None:
        # Traced before dispatch, so presses the StatusPanel/SprintList stop are recorded too. The trace is
        # evidence only: nothing in it may raise into Textual's event path.
        if isinstance(event, events.InputEvent) and not event.is_forwarded:
            try:
                self._trace_event(event)
            except Exception:  # noqa: BLE001, S110
                pass
        await super().on_event(event)

    def _trace_event(self, event: events.InputEvent) -> None:
        active = self.query_one(TabbedContent).active
        if isinstance(event, (events.MouseDown, events.MouseUp)):
            kind = "mouse_down" if isinstance(event, events.MouseDown) else "mouse_up"
            try:
                widget, _ = self.screen.get_widget_at(event.screen_x, event.screen_y)
                hit = f"{type(widget).__name__}#{widget.id}"
            except NoWidget:
                hit = "off-screen"
            self._trace_input(f"{kind} x={event.screen_x} y={event.screen_y} button={event.button} widget={hit} active={active}")
        elif isinstance(event, events.Key):
            self._trace_input(f"key {event.key} active={active}")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._trace_input(f"tab {event.pane.id}")
        if event.pane.id == "prs":
            self.tick(True)
        elif event.pane.id == "status":
            self.call_after_refresh(self._refresh_status_viewport)
        elif event.pane.id == "diagnostics":
            self.refresh_views()
        elif event.pane.id == "quality":
            self._load_closed_quality()

    @work(thread=True, group="quality", exclusive=True, exit_on_error=False)
    def _load_closed_quality(self) -> None:
        result = ledger_source.closed_quality(self.config.repo_root, self.config.phase)
        self.call_from_thread(self._finish_quality, result)

    def _finish_quality(self, result) -> None:
        if not self.is_running:
            return
        previous = (self._quality_error, self._closed_quality_runs, self._closed_findings)
        if isinstance(result, Err):
            self._quality_error = str(result.error)
        else:
            self._quality_error = None
            self._closed_quality_runs, self._closed_findings = result.value
            self._combine_quality()
        if previous != (self._quality_error, self._closed_quality_runs, self._closed_findings):
            self.refresh_views()

    def _combine_quality(self) -> None:
        qa = {run.bead.get("id"): run.bead for run in (*self._closed_quality_runs, *self._open_quality_runs)}
        findings = {row.get("id"): row for row in (*self._closed_findings, *self._open_findings)}
        self.quality_runs = runs.qa_runs(list(qa.values()))
        self.finding_beads = list(findings.values())

    def tick(self, force_github: bool) -> None:
        # Interval ticks are intentionally dropped while work is in flight. Forced
        # refreshes are coalesced into one follow-up after this result is displayed.
        if self._tick_running:
            self._pending_forced_refresh |= force_github
            return
        self._tick_running = True
        self._run_tick(force_github)

    @work(thread=True, group="tick", exclusive=True, exit_on_error=False)
    def _run_tick(self, force_github: bool) -> None:
        snapshot: Snapshot | None = None
        text = ""
        error: Error | None = None
        tick_error: Error | None = None
        run_data: tuple[list[runs.SanityRun], list[runs.QaRun], list[dict]] | None = None
        runs_error = None
        self._step_lock.acquire()
        try:
            step_result = self.step(self.config, self.state, force_github)
            snapshot, state = step_result
            self.state = state
            run_data = step_result.run_data
            runs_error = step_result.runs_error
        except Exception as step_error:  # noqa: BLE001
            tick_error = IoFailed("io_failed", "console tick", str(step_error), "retry the console tick")
        finally:
            self._step_lock.release()
        if snapshot is not None:
            try:
                text, error = status.status_text(snapshot)
            except Exception as render_error:  # noqa: BLE001
                tick_error = IoFailed("io_failed", "console status rendering", str(render_error), "retry the console tick")
        self.call_from_thread(self._finish_tick, snapshot, text, error, tick_error, run_data, runs_error)

    def _finish_tick(self, snapshot: Snapshot | None, text: str, error: Error | None, tick_error: Error | None,
                     run_data: tuple[list[runs.SanityRun], list[runs.QaRun], list[dict]] | None,
                     runs_error: str | None) -> None:
        if run_data is not None:
            self.sanity_runs, self._open_quality_runs, self._open_findings = run_data
            self._combine_quality()
        self.runs_error = runs_error
        if tick_error is not None:
            self._show_tick_error(tick_error)
        elif snapshot is not None:
            self._show(snapshot, text, error)
        self._tick_running = False
        if self._pending_forced_refresh:
            self._pending_forced_refresh = False
            self.tick(True)

    def _show_tick_error(self, error: Error) -> None:
        if not self.is_running:
            return
        self.tick_error = error
        logger = create_console_logger(self.config.repo_root)
        if isinstance(logger, Ok):
            logger.value.event("error", level="error", message=error_message(asdict(error)), error=asdict(error))
            logger.value.flush()
        self.refresh_views()

    def _show(self, snapshot: Snapshot, text: str, error: Error | None) -> None:
        # A tick can finish after q has begun shutdown; its screen is gone, so there is nothing to draw.
        if not self.is_running:
            return
        if self.startup_error is not None:
            snapshot = replace(snapshot, errors=(self.startup_error, *snapshot.errors))
        self.snapshot, self.status_text, self.status_error, self.tick_error = snapshot, text, error, None
        sprints = self.query_one("#sprints", OptionList)
        highlighted = sprints.highlighted
        sprints.clear_options()
        options = []
        for sprint in snapshot.sprints:
            glyph, style = SPRINT_ICONS[sprint.state]
            label = Text()
            label.append(glyph, style=style)
            label.append(f" {sprint.id}  {sprint.title}")
            options.append(Option(label, id=sprint.id))
        sprints.add_options(options)
        if highlighted is not None and highlighted < len(snapshot.sprints):
            sprints.highlighted = highlighted
        elif snapshot.sprints:
            sprints.highlighted = next((index for index, sprint in enumerate(snapshot.sprints) if sprint.state != SprintState.CLOSED), 0)
        self.refresh_views()
        # Panel sizes settle after the first layout; render once more at the laid-out size.
        self.call_after_refresh(self.refresh_views)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "quality-runs":
            self._show_findings(event.option.id)
        else:
            self._show_sprint(event.option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "quality-runs":
            self._show_findings(event.option.id)
        else:
            self._show_sprint(event.option.id)

    def _show_findings(self, bead_id: str | None) -> None:
        run = next((r for r in self.quality_runs if r.bead.get("id") == bead_id), None)
        if run is None:
            return
        try:
            findings = runs.run_findings(run.bead, self.finding_beads)
            agents = self.snapshot.agents if self.snapshot else ()
            qa_state = run.verdict
            colors: dict[str, str] = {}
            display_findings = []
            for finding in findings:
                name = str(finding.get("reviewer") or "")
                roster = tuple(replace(agent, qa_state=qa_state)
                               if agent.name == name and agent_group(agent.agent_type) == "qa" else agent
                               for agent in agents)
                label = _agent_name(name, roster)
                display_findings.append({**finding, "reviewer_display": label.plain})
                member = next((agent for agent in roster if agent.name == name), None)
                _, color = icon_for(member)
                if name and color:
                    colors[label.plain] = color
            content = findings_renderer.render_findings(self.config.repo_root, display_findings)
        except Exception as error:  # noqa: BLE001
            colors = {}
            content = f"Unable to render findings: {error}"
        try:
            widget = self.query_one("#quality-findings", FindingsMarkdown)
        except NoMatches:
            return  # A queued highlight can arrive while the screen is shutting down.
        widget.reviewer_colors = colors
        widget.update(content)

    def _show_sprint(self, sprint_id: str | None) -> None:
        if self.snapshot is None:
            return
        sprint = next((value for value in self.snapshot.sprints if value.id == sprint_id), None)
        self.query_one("#sprint-steps-text", Static).update(
            sprint_steps(sprint, self.snapshot.agents) if sprint else ""
        )

    def on_resize(self) -> None:
        self.call_after_refresh(self._place_build_line)
        self.call_after_refresh(self.refresh_views)

    def on_status_panel_resized(self) -> None:
        self.call_after_refresh(self._refresh_status_viewport)

    def _refresh_status_viewport(self) -> None:
        if self.query_one(TabbedContent).active != "status":
            return
        panel = self.query_one("#status-panel", ScrollableContainer)
        width = panel.scrollable_content_region.width
        if width <= 0:
            return
        lines = self.status_text.splitlines()
        self.query_one("#status-overflow").display = max((cell_len(line) for line in lines), default=0) > width
        if not self._status_view_initialized:
            first_line = next((line for line in lines if line.strip()), None)
            if first_line is not None:
                if panel.virtual_size.width <= 0:
                    self.call_after_refresh(self._refresh_status_viewport)
                    return
                indent = first_line[:len(first_line) - len(first_line.lstrip())]
                panel.scroll_to(x=cell_len(indent), animate=False, immediate=True)
                self._status_view_initialized = True

    def refresh_views(self) -> None:
        snapshot = self.snapshot
        if snapshot is None and self.tick_error is None:
            return
        width = self.size.width
        panel = self.query_one("#dispatch-panel")
        dashboard_width = min(panel.size.width or width, width)
        if snapshot is not None:
            height = panel.size.height or self.size.height
            table = self.query_one("#status-table", Static)
            table.display = snapshot.phase_data is not None
            if snapshot.phase_data is not None:
                table.update(status_table.rich_table(status_table.rows(snapshot)))
            self.query_one("#dashboard", Static).update(render.dashboard(snapshot, dashboard_width, height))
            self.query_one("#prs-text", Static).update(prs.render_prs(snapshot, width, self.show_pr_history))
            self.query_one("#status-bar", Static).update(render.status_bar(snapshot, width))
            header = status_header(snapshot)
            header.no_wrap = True
            header.overflow = "ellipsis"
            if self.status_error is not None:
                header.append(f"  {self.status_error.kind}: {self.status_error.fix}", style="red")
            header.truncate(width, overflow="ellipsis")
            self.query_one("#status-header", Static).update(header)
            self.query_one("#status-text", Static).update(Text(self.status_text, no_wrap=True))
            sanity, errors = sanity_text(self.sanity_runs, snapshot.agents)
            if self.runs_error:
                sanity.append(f"\nUnable to load runs: {self.runs_error}", style="red")
            self.query_one("#sanity-list", Static).update(sanity)
            self.query_one("#sanity-errors", Static).update(errors)
            quality_error = self.query_one("#quality-error", Static)
            quality_message = self._quality_error or self.runs_error
            quality_error.update(Text(f"Unable to load runs: {quality_message}", style="red") if quality_message else "")
            quality_error.display = bool(quality_message)
            quality = self.query_one("#quality-runs", OptionList)
            quality_header = self.query_one("#quality-runs-header", Static)
            highlighted = quality.highlighted
            quality.clear_options()
            rows = []
            for run in self.quality_runs:
                findings = runs.run_findings(run.bead, self.finding_beads)
                rows.append((run, findings, _quality_run_cells(run, findings)))
            widths = _quality_widths([cells for _, _, cells in rows])
            quality_header.update(_quality_header(widths))
            options = []
            for run, findings, (cells, verdict, blocking) in rows:
                label = _quality_row(cells, verdict, blocking, widths)
                options.append(Option(label, id=str(run.bead.get("id"))))
            quality.add_options(options)
            if options:
                quality.highlighted = min(highlighted or 0, len(options) - 1)
            self.call_after_refresh(self._refresh_status_viewport)
        if self.tick_error is not None:
            dashboard, header = render.tick_error(self.tick_error, dashboard_width)
            self.query_one("#dashboard", Static).update(dashboard)
            self.query_one("#status-header", Static).update(header)
        details = "No errors recorded."
        if self.query_one(TabbedContent).active == "diagnostics":
            logger = create_console_logger(self.config.repo_root)
            if isinstance(logger, Ok):
                queried = logger.value.query(levels=("warn", "error"), limit=20, order="newest_first")
                if isinstance(queried, Ok):
                    lines = [_diagnostic_line(event) for event in queried.value]
                    details = "\n".join(lines) or details
        agent_details = []
        if self.snapshot is not None:
            agent_details = [render.render_agent(agent.name, agent).plain for agent in self.snapshot.agents]
        agents = "Agents: " + ", ".join(agent_details) if agent_details else "Agents: none"
        self.query_one("#diagnostics-text", Static).update(
            f"Build: {display_identity()}\nInput trace: {self.input_log_path}\n{agents}\n\n{details}")


RunnerStep = Callable[[Config, State, bool, StateStore], StepResult]


def run(config: Config, step: StepFn | None, state: State, startup_error: Error | None = None, *, runner_step: RunnerStep | None = None) -> int:
    """Take the console lock, then run the app until q; a held lock exits 1 before drawing."""
    root_id = Path(config.state_dir).name
    lock = acquire_lock(config.repo_root, root_id)
    if isinstance(lock, Err):
        print(_lock_message(lock.error), file=sys.stderr)
        return 1
    with lock.value:
        if runner_step is not None:
            saved = lock.value.load_state()
            state = saved.value if isinstance(saved, Ok) else INITIAL_STATE
        bound_step = step if runner_step is None else lambda current, current_state, force: runner_step(current, current_state, force, lock.value)
        assert bound_step is not None
        result = ConsoleApp(config, bound_step, state, startup_error).run()
    return result or 0
