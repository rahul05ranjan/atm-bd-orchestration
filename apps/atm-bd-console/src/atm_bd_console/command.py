"""The one subprocess runner for the source modules: a timeout on every call and one failure shape."""
from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .result import CommandFailed, Err, Ok, Result, Timeout

DEFAULT_TIMEOUT_S = 20
# Exit status a shell uses for "program not found"; a missing or unexecutable program is reported with it.
MISSING_PROGRAM_CODE = 127
# Output that is not text has no exit status either; the decode failure is reported with this code.
NO_EXIT_CODE = -1


@dataclass(frozen=True)
class Output:
    code: int
    stdout: str


def run(argv: Sequence[str], fix: str, *, cwd: str | Path | None = None, timeout_s: int = DEFAULT_TIMEOUT_S,
        ok_codes: tuple[int, ...] = (0,), stdin: str | None = None,
        env: Mapping[str, str] | None = None) -> Result[Output]:
    """Run `argv`; an exit code outside `ok_codes` is CommandFailed carrying stderr (stdout when stderr is empty),
    stripped; an expired timeout is Timeout; a missing program is CommandFailed with code 127; output that does
    not decode is CommandFailed with code -1. `stdin` is sent as text; `env` replaces the child's environment.
    `fix` is the caller's own advice for every failure."""
    command = tuple(argv)
    try:
        done = subprocess.run(command, cwd=cwd, input=stdin, env=env, capture_output=True, text=True, check=False,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Err(Timeout("timeout", command, timeout_s, fix))
    except OSError as error:
        return Err(CommandFailed("command_failed", command, MISSING_PROGRAM_CODE, str(error), fix))
    except UnicodeDecodeError as error:
        return Err(CommandFailed("command_failed", command, NO_EXIT_CODE, str(error), fix))
    if done.returncode not in ok_codes:
        return Err(CommandFailed("command_failed", command, done.returncode, (done.stderr or done.stdout).strip(), fix))
    return Ok(Output(done.returncode, done.stdout))
