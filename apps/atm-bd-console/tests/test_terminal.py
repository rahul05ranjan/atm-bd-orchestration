from __future__ import annotations

import asyncio
import sys

import pytest
from conftest import FakeStep
from contract_samples import CONFIG, STATE
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
