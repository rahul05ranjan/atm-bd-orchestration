"""The one subprocess runner for the source modules: a timeout on every call and one failure shape."""
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .result import CommandFailed, Err, Ok, Result, Timeout

DEFAULT_TIMEOUT_S = 20


@dataclass(frozen=True)
class Output:
    code: int
    stdout: str


def run(argv: Sequence[str], fix: str, *, cwd: str | Path | None = None, timeout_s: int = DEFAULT_TIMEOUT_S,
        ok_codes: tuple[int, ...] = (0,)) -> Result[Output]:
    """Run `argv`; an exit code outside `ok_codes` is CommandFailed carrying stderr (stdout when stderr is empty),
    stripped; an expired timeout is Timeout; a missing program is CommandFailed with code 127. `fix` is the
    caller's own advice for both."""
    command = tuple(argv)
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Err(Timeout("timeout", command, timeout_s, fix))
    except OSError as error:
        return Err(CommandFailed("command_failed", command, 127, str(error), fix))
    if done.returncode not in ok_codes:
        return Err(CommandFailed("command_failed", command, done.returncode, (done.stderr or done.stdout).strip(), fix))
    return Ok(Output(done.returncode, done.stdout))
