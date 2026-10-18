from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from conftest import FakeStep
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE
from rich.cells import cell_len

from atm_bd_console.app import ConsoleApp

FIXTURE = json.loads((Path(__file__).parent / "fixtures/bh-wide-dag.json").read_text())
GRAPH = FIXTURE["text"]
ANCHOR = 204  # the indent of the first graph line: the scroll that puts the root at the left edge
ROOT_LABEL = "atm-bh-1"
ROOT_COLUMN = next(cell_len(line[: line.index(ROOT_LABEL)]) for line in GRAPH.splitlines() if ROOT_LABEL in line)


class FakePanel:
    """Scroll state the test sets directly; Textual layout and frame timing are not involved."""

    def __init__(self, width: int, virtual_width: int) -> None:
        self.scrollable_content_region = SimpleNamespace(width=width)
        self.virtual_size = SimpleNamespace(width=virtual_width)
        self.scroll_x = 0
        self.scroll_calls: list[int] = []

    @property
    def max_scroll_x(self) -> int:
        return max(0, self.virtual_size.width - self.scrollable_content_region.width)

    def scroll_to(self, x: int, animate: bool, immediate: bool) -> None:
        self.scroll_calls.append(x)
        self.scroll_x = min(x, self.max_scroll_x)  # the scroll target is clamped to the scroll range

    def resize(self, width: int, virtual_width: int) -> None:
        self.scrollable_content_region = SimpleNamespace(width=width)
        self.virtual_size = SimpleNamespace(width=virtual_width)
        self.scroll_x = min(self.scroll_x, self.max_scroll_x)  # layout clamps the position to the new range


def status_app(panel: FakePanel) -> SimpleNamespace:
    app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(RUNNING), STATE)
    app.status_text = GRAPH
    ui = SimpleNamespace(
        app=app, panel=panel, overflow=SimpleNamespace(display=False),
        tabs=SimpleNamespace(active="status"), scheduled=[],
    )

    def query_one(selector, *_):
        if selector == "#status-panel":
            return ui.panel
        if selector == "#status-overflow":
            return ui.overflow
        return ui.tabs

    app.query_one = query_one
    app.call_after_refresh = lambda callback, *_: ui.scheduled.append(callback)
    return ui


def run_scheduled(ui: SimpleNamespace) -> None:
    pending, ui.scheduled[:] = list(ui.scheduled), []
    for callback in pending:
        callback()


def test_fixture_is_the_wide_graph() -> None:
    assert hashlib.sha256(GRAPH.encode()).hexdigest() == FIXTURE["sha256"]
    assert max(cell_len(line) for line in GRAPH.splitlines()) == 508


def test_anchor_waits_for_a_laid_out_viewport() -> None:
    ui = status_app(FakePanel(width=140, virtual_width=0))  # before layout the scroll range is empty

    ui.app._refresh_status_viewport()

    assert ui.panel.scroll_calls == []
    assert not ui.app._status_view_initialized
    assert ui.scheduled == [ui.app._refresh_status_viewport]

    ui.panel.resize(width=140, virtual_width=508)  # layout measures the wide graph
    run_scheduled(ui)

    assert ui.panel.scroll_calls == [ANCHOR]
    assert ui.panel.scroll_x == ANCHOR
    assert ui.app._status_view_initialized
    assert ui.scheduled == []


def test_wide_graph_anchor_pan_tab_switch_and_resize_are_deterministic() -> None:
    ui = status_app(FakePanel(width=140, virtual_width=0))
    panel, app = ui.panel, ui.app

    app._refresh_status_viewport()  # before layout
    panel.resize(width=140, virtual_width=508)
    run_scheduled(ui)

    # Root anchored at the left of the 140-wide viewport, and the overflow indicator is shown.
    assert panel.scroll_x == ANCHOR
    assert 0 <= ROOT_COLUMN - panel.scroll_x
    assert ROOT_COLUMN - panel.scroll_x + cell_len(ROOT_LABEL) <= panel.scrollable_content_region.width
    assert ui.overflow.display

    panel.scroll_x = 344  # the user pans right
    app._refresh_status_viewport()  # a refresh keeps the pan
    assert panel.scroll_x == 344

    ui.tabs.active = "dispatch"
    app._refresh_status_viewport()  # not the status tab: nothing changes
    ui.tabs.active = "status"
    app._refresh_status_viewport()  # coming back keeps the pan
    assert panel.scroll_x == 344
    assert panel.scroll_calls == [ANCHOR]

    panel.resize(width=540, virtual_width=540)  # the terminal grows past the graph
    app._refresh_status_viewport()

    assert panel.max_scroll_x == 0
    assert panel.scroll_x == 0
    assert not ui.overflow.display
    assert app.status_text == GRAPH
