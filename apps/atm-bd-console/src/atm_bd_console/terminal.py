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
    from textual.drivers.linux_driver import LinuxDriver

    class CellMouseDriver(LinuxDriver):
        """LinuxDriver that never requests in-band resize or pixel mouse reporting."""

        def _query_in_band_window_resize(self) -> None:
            return None

        def _enable_in_band_window_resize(self) -> None:
            return None

        def _enable_mouse_pixels(self) -> None:
            return None

    CONSOLE_DRIVER_CLASS = CellMouseDriver
