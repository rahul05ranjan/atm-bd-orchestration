"""Bound child processes in sanity integration tests."""
import subprocess


COMMAND_TIMEOUT = 30
HANG_GUARD_SECONDS = 60


def run_bounded(argv, *, timeout=COMMAND_TIMEOUT, runner=None, **kwargs):
    if kwargs.get("input") is None and "stdin" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    try:
        return (runner or subprocess.run)(argv, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"{argv!r} timed out after {timeout}s; stdout={error.stdout!r}; stderr={error.stderr!r}") from error
