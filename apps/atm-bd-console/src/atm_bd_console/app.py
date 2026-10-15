"""The interactive console: one Textual app over an injected step function."""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll
from textual.widgets import OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from . import prs, render, status
from .result import Err, Error, IoFailed
from .state import StateStore, acquire_lock
from .types import Config, Snapshot, SprintView, State, StepFn

STEP_KINDS = ("dev", "sanity", "qa", "fix")


def status_header(snapshot: Snapshot) -> str:
    """sprints N/M · ✅ x · 🌀 y · ⏳ z; ⏳ counts every sprint neither closed nor in progress."""
    states = [sprint.state for sprint in snapshot.sprints]
    closed, running = states.count("closed"), states.count("in_progress")
    waiting = len(states) - closed - running
    return f"sprints {closed}/{len(states)} · ✅ {closed} · 🌀 {running} · ⏳ {waiting}"


def sprint_steps(sprint: SprintView) -> Text:
    lines = [Text(f"{sprint.id}  {sprint.title}", no_wrap=True)]
    for step in sprint.steps:
        if step.kind in STEP_KINDS:
            lines.append(Text(f"  {step.kind:<6} {step.bead_id}  {step.status}  {step.assignee or 'unassigned'}",
                              no_wrap=True))
    return Text("\n").join(lines)


def _lock_message(error: Error) -> str:
    if error.kind == "lock_held":
        age = "no snapshot" if error.snapshot_age_s is None else f"snapshot age {error.snapshot_age_s}s"
        return f"atm-bd-console: lock held by a running console ({age}); {error.fix}"
    return f"atm-bd-console: {error.kind}: {error.path}: {error.message}; {error.fix}"


class ConsoleApp(App[int]):
    CSS = """
    TabbedContent, TabPane { height: 1fr; }
    #dispatch-panel { height: 1fr; }
    #status-panel { overflow: auto auto; height: 1fr; }
    #status-text { width: auto; height: auto; }
    #sprints { height: auto; max-height: 12; }
    #status-bar { dock: bottom; height: 1; }
    """
    # Only q quits (ANC-014): Textual's default ctrl+q quit binding is replaced.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+q", "noop", show=False, priority=True),
    ]

    def __init__(self, config: Config, step: StepFn, state: State, startup_error: Error | None = None) -> None:
        super().__init__()
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

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="dispatch"):
            with TabPane("Dispatch", id="dispatch"), VerticalScroll(id="dispatch-panel"):
                yield Static(id="dashboard")
            with TabPane("Status", id="status"):
                yield Static(id="status-header")
                yield OptionList(id="sprints")
                yield Static(id="sprint-steps")
                with ScrollableContainer(id="status-panel"):
                    yield Static(id="status-text")
            with TabPane("PRs", id="prs"), VerticalScroll():
                yield Static(id="prs-text")
        yield Static(id="status-bar")

    def on_mount(self) -> None:
        self.tick(False)
        self.set_interval(self.config.interval_s, self._on_interval)

    def _on_interval(self) -> None:
        # A stopped snapshot ends ticking until r.
        if self.snapshot is None or self.snapshot.stopped is None:
            self.tick(False)

    def action_refresh(self) -> None:
        self.tick(True)

    def action_noop(self) -> None:
        return None

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "prs":
            self.tick(True)

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
        self._step_lock.acquire()
        try:
            snapshot, state = self.step(self.config, self.state, force_github)
            self.state = state
        except Exception as step_error:  # noqa: BLE001
            tick_error = IoFailed("io_failed", "console tick", str(step_error), "retry the console tick")
        finally:
            self._step_lock.release()
        if snapshot is not None:
            try:
                text, error = status.status_text(snapshot)
            except Exception as render_error:  # noqa: BLE001
                tick_error = IoFailed("io_failed", "console status rendering", str(render_error), "retry the console tick")
        self.call_from_thread(self._finish_tick, snapshot, text, error, tick_error)

    def _finish_tick(self, snapshot: Snapshot | None, text: str, error: Error | None, tick_error: Error | None) -> None:
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
        sprints.add_options([Option(f"{sprint.id}  {sprint.title}", id=sprint.id) for sprint in snapshot.sprints])
        if highlighted is not None and highlighted < len(snapshot.sprints):
            sprints.highlighted = highlighted
        self.refresh_views()
        # Panel sizes settle after the first layout; render once more at the laid-out size.
        self.call_after_refresh(self.refresh_views)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_sprint(event.option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._show_sprint(event.option.id)

    def _show_sprint(self, sprint_id: str | None) -> None:
        if self.snapshot is None:
            return
        sprint = next((value for value in self.snapshot.sprints if value.id == sprint_id), None)
        self.query_one("#sprint-steps", Static).update(sprint_steps(sprint) if sprint else "")

    def on_resize(self) -> None:
        self.call_after_refresh(self.refresh_views)

    def refresh_views(self) -> None:
        snapshot = self.snapshot
        if snapshot is None and self.tick_error is None:
            return
        width = self.size.width
        panel = self.query_one("#dispatch-panel")
        dashboard_width = min(panel.size.width or width, width)
        if snapshot is not None:
            height = panel.size.height or self.size.height
            self.query_one("#dashboard", Static).update(render.dashboard(snapshot, dashboard_width, height))
            self.query_one("#prs-text", Static).update(prs.render_prs(snapshot, width))
            self.query_one("#status-bar", Static).update(render.status_bar(snapshot, width))
            header = Text(status_header(snapshot), no_wrap=True, overflow="ellipsis")
            if self.status_error is not None:
                header.append(f"  {self.status_error.kind}: {self.status_error.fix}", style="red")
            header.truncate(width, overflow="ellipsis")
            self.query_one("#status-header", Static).update(header)
            self.query_one("#status-text", Static).update(Text(self.status_text, no_wrap=True))
        if self.tick_error is not None:
            dashboard, header = render.tick_error(self.tick_error, dashboard_width)
            self.query_one("#dashboard", Static).update(dashboard)
            self.query_one("#status-header", Static).update(header)


RunnerStep = Callable[[Config, State, bool, StateStore], tuple[Snapshot, State]]


def run(config: Config, step: StepFn | None, state: State, startup_error: Error | None = None, *, runner_step: RunnerStep | None = None) -> int:
    """Take the console lock, then run the app until q; a held lock exits 1 before drawing."""
    root_id = Path(config.state_dir).name
    lock = acquire_lock(config.repo_root, root_id)
    if isinstance(lock, Err):
        print(_lock_message(lock.error), file=sys.stderr)
        return 1
    with lock.value:
        bound_step = step if runner_step is None else lambda current, current_state, force: runner_step(current, current_state, force, lock.value)
        assert bound_step is not None
        result = ConsoleApp(config, bound_step, state, startup_error).run()
    return result or 0
