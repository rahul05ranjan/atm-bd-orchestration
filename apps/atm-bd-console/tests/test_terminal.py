from __future__ import annotations

import asyncio
import sys

import pytest
from conftest import FakeStep
from contract_samples import CONFIG, STATE
from textual import events
from textual._xterm_parser import XTermParser
from textual.drivers import linux_driver
from textual.messages import InBandWindowResize

from atm_bd_console.app import ConsoleApp
from atm_bd_console.terminal import CONSOLE_DRIVER_CLASS

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the console driver is the POSIX Linux driver")

PIXEL_MOUSE_HANDSHAKE = ("\x1b[?2048$p", "\x1b[?2048h", "\x1b[?1016h")


def test_console_app_runs_on_the_cell_mouse_driver() -> None:
    from atm_bd_console.terminal import CellMouseDriver

    app = ConsoleApp(CONFIG, FakeStep(), STATE)
    assert CONSOLE_DRIVER_CLASS is CellMouseDriver and app.driver_class is CellMouseDriver


def test_cell_mouse_driver_never_requests_in_band_resize_or_pixel_mouse() -> None:
    # #241: a terminal that answers the 2048 query but reports cell coordinates makes Textual scale every click
    # to row 0 (the first tab header). The console's driver never starts that handshake, even if asked.
    app = ConsoleApp(CONFIG, FakeStep(), STATE)
    written: list[str] = []

    async def drive() -> None:  # the driver binds the running loop
        driver = CONSOLE_DRIVER_CLASS(app)
        driver.write = written.append  # type: ignore[method-assign]
        driver._query_in_band_window_resize()
        driver.process_message(InBandWindowResize(supported=True, enabled=False))
        driver._enable_mouse_pixels()
        assert driver._in_band_window_resize is False and driver._mouse_pixels is False

    asyncio.run(drive())
    assert not any(sequence in "".join(written) for sequence in PIXEL_MOUSE_HANDSHAKE), written


IN_BAND_RESIZE_REPORT = "\x1b[48;40;120;1200;800t"  # rows;cols;pixel height;pixel width, as a terminal in mode 2048 sends
SGR_CLICK = "\x1b[<0;60;15M"


def _mouse_downs(parser: XTermParser, data: str) -> list[tuple[int, int]]:
    return [(int(event.x), int(event.y)) for event in parser.feed(data) if isinstance(event, events.MouseDown)]


def test_cell_mouse_parser_keeps_cells_after_an_unsolicited_in_band_resize_report() -> None:
    from atm_bd_console.terminal import CellMouseParser

    # QA .4.35 F1: the stock parser scales the same click to the tab header row once a resize report arrived.
    stock = XTermParser()
    list(stock.feed(IN_BAND_RESIZE_REPORT + SGR_CLICK))  # feed is lazy; the report must be consumed
    assert _mouse_downs(stock, SGR_CLICK) == [(8, 0)]
    cells = CellMouseParser()
    list(cells.feed(IN_BAND_RESIZE_REPORT + SGR_CLICK))
    assert _mouse_downs(cells, SGR_CLICK) == [(59, 14)]


def test_input_thread_builds_the_cell_mouse_parser_and_restores_textual_afterwards() -> None:
    from atm_bd_console.terminal import CellMouseParser, cell_mouse_parser

    assert linux_driver.XTermParser is XTermParser
    with cell_mouse_parser():
        assert linux_driver.XTermParser is CellMouseParser
    assert linux_driver.XTermParser is XTermParser
    assert "run_input_thread" in CONSOLE_DRIVER_CLASS.__dict__
