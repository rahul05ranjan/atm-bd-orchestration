"""Terminal driver for the console: mouse reports stay in cells.

Textual's Linux driver asks the terminal for in-band window resize (DEC mode 2048) and, when the
terminal says yes, also switches mouse reporting to pixel coordinates (mode 1016) and divides every
mouse position by the pixel/cell ratio of the last resize report. A terminal (or a multiplexer in
front of it) that answers the 2048 query but keeps sending cell coordinates then lands every click
on row 0: the first tab's header. That is #241 (clicks on the Status tab jumped to Dispatch, sprint
rows could not be selected, drag selection never copied). The console never enters that protocol:
window resizes arrive through SIGWINCH as before, and mouse positions are always cells.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":  # pragma: no cover - the console runs on POSIX terminals
    CONSOLE_DRIVER_CLASS = None
else:
    from collections.abc import Iterator
    from contextlib import contextmanager

    from textual._xterm_parser import XTermParser
    from textual.drivers import linux_driver
    from textual.drivers.linux_driver import LinuxDriver
    from textual.message import Message
    from textual.messages import InBandWindowResize

    class CellMouseParser(XTermParser):
        """XTermParser whose mouse positions are always cells.

        The stock parser flips itself into pixel scaling on any in-band resize report (CSI 48 ; rows ; cols ;
        height ; width t), which a terminal or multiplexer left in mode 2048 by an earlier session still sends.
        """

        def parse_mouse_code(self, code: str) -> Message | None:
            self.mouse_pixels = False
            return super().parse_mouse_code(code)

    @contextmanager
    def cell_mouse_parser() -> Iterator[None]:
        """Make the driver's input thread build a CellMouseParser.

        Textual's run_input_thread names its parser class through the linux_driver module global, so that
        name is the only hook; it is swapped for the life of the input thread and restored afterwards.
        """
        stock = linux_driver.XTermParser
        linux_driver.XTermParser = CellMouseParser
        try:
            yield
        finally:
            linux_driver.XTermParser = stock

    class CellMouseDriver(LinuxDriver):
        """LinuxDriver that never requests in-band resize or pixel mouse reporting."""

        def run_input_thread(self) -> None:
            with cell_mouse_parser():
                super().run_input_thread()

        def _query_in_band_window_resize(self) -> None:
            return None

        def process_message(self, message: Message) -> None:
            # Never asked for, so never honoured: the stock handler would flag in-band resize (silencing
            # SIGWINCH resizes) and enable pixel mouse reporting.
            if isinstance(message, InBandWindowResize):
                return
            super().process_message(message)

        def _enable_in_band_window_resize(self) -> None:
            return None

        def _enable_mouse_pixels(self) -> None:
            return None

    CONSOLE_DRIVER_CLASS = CellMouseDriver
