"""Bound subprocesses used by the package's installer and installed-suite tests."""

import subprocess

import pytest

COMMAND_TIMEOUT = 30
SUITE_TIMEOUT = 180
OUTPUT_TAIL = 16000


def run_command(command, *, timeout=COMMAND_TIMEOUT, check=False, **kwargs):
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    try:
        return subprocess.run(command, timeout=timeout, check=check, **kwargs)
    except subprocess.TimeoutExpired as error:
        def tail(output):
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return (output or "")[-OUTPUT_TAIL:]

        pytest.fail(
            f"Command {command!r} timed out after {timeout}s\n"
            f"stdout tail:\n{tail(error.stdout)}\nstderr tail:\n{tail(error.stderr)}",
            pytrace=False,
        )
