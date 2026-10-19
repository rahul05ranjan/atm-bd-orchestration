"""Bound child processes in sanity integration tests."""
import subprocess


HANG_GUARD_SECONDS = 60


def run_bounded(argv, *, runner=None, **kwargs):
    try:
        return (runner or subprocess.run)(argv, timeout=HANG_GUARD_SECONDS, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"command timed out: {argv!r}") from exc
