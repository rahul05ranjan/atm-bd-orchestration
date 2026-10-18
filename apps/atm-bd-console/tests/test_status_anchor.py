from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from conftest import FakeStep
from contract_fixtures import RUNNING
from contract_samples import CONFIG, STATE

from atm_bd_console.app import ConsoleApp

GRAPH = json.loads((Path(__file__).parent / "fixtures/bh-wide-dag.json").read_text())["text"]
ANCHOR = 204  # the root column of the wide Herdr graph


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


def status_app(panel: FakePanel) -> tuple[ConsoleApp, list]:
    app = ConsoleApp(replace(CONFIG, interval_s=3600), FakeStep(RUNNING), STATE)
    app.status_text = GRAPH
    overflow = SimpleNamespace(display=False)
    tabs = SimpleNamespace(active="status")

    def query_one(selector, *_):
        if selector == "#status-panel":
            return panel
        if selector == "#status-overflow":
            return overflow
        return tabs

    scheduled: list = []
    app.query_one = query_one
    app.call_after_refresh = lambda callback, *_: scheduled.append(callback)
    return app, scheduled


def test_anchor_waits_for_a_laid_out_viewport() -> None:
    panel = FakePanel(width=140, virtual_width=0)  # before layout the scroll range is empty
    app, scheduled = status_app(panel)

    app._refresh_status_viewport()

    assert panel.scroll_calls == []
    assert not app._status_view_initialized
    assert scheduled == [app._refresh_status_viewport]

    panel.virtual_size = SimpleNamespace(width=508)  # layout measures the wide graph
    pending, scheduled[:] = list(scheduled), []
    for callback in pending:
        callback()

    assert panel.scroll_calls == [ANCHOR]
    assert panel.scroll_x == ANCHOR
    assert app._status_view_initialized
    assert scheduled == []


def test_anchor_is_applied_once_and_a_user_pan_is_kept() -> None:
    panel = FakePanel(width=140, virtual_width=508)
    app, scheduled = status_app(panel)

    app._refresh_status_viewport()
    assert panel.scroll_calls == [ANCHOR]

    panel.scroll_x = 344  # the user pans right
    app._refresh_status_viewport()

    assert panel.scroll_calls == [ANCHOR]
    assert panel.scroll_x == 344
    assert scheduled == []
