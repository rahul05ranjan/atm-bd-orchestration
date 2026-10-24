import os
import select
import subprocess
import sys

import pytest
from subprocess_helpers import COMMAND_TIMEOUT, HANG_GUARD_SECONDS, OUTPUT_TAIL, SUITE_TIMEOUT, run_command


def test_run_command_uses_timeout_and_captures_output(monkeypatch):
    result = subprocess.CompletedProcess(["git", "status"], 0, b"ok", b"")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return result

    monkeypatch.setattr(subprocess, "run", run)
    assert run_command(["git", "status"], check=True) is result
    assert calls == [(["git", "status"], {
        "timeout": COMMAND_TIMEOUT, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL, "check": True,
    })]


def test_run_command_respects_input_capture_and_injected_runner():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return "result"

    assert run_command(["command"], timeout=60, runner=runner, input="payload", capture_output=True) == "result"
    assert run_command(["command"], runner=runner, input=None, capture_output=True) == "result"
    assert calls == [
        (["command"], {"timeout": 60, "check": False, "input": "payload", "capture_output": True}),
        (["command"], {"timeout": 30, "check": False, "input": None, "capture_output": True,
                       "stdin": subprocess.DEVNULL}),
    ]


@pytest.mark.parametrize("output", [b"x" * OUTPUT_TAIL + b"stdout-end", "x" * OUTPUT_TAIL + "stdout-end"])
def test_run_command_reports_timeout_with_output_tail(monkeypatch, output):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 12
        raise subprocess.TimeoutExpired(command, 12, output=output, stderr=b"stderr-end\xff")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(pytest.fail.Exception) as failure:
        run_command(["pytest", "path/to/suite"], timeout=12, text=True)
    message = str(failure.value)
    assert "path/to/suite" in message
    assert "12s" in message
    assert "stdout-end" in message and "stderr-end\ufffd" in message
    assert "x" * (OUTPUT_TAIL + 1) not in message


def test_installed_suite_timeout_reports_blocked_child_output():
    assert 0 < SUITE_TIMEOUT < 30 * 60
    ready_read, ready_write = os.pipe()
    block_read, block_write = os.pipe()
    def gated_runner(command, *, timeout, check, **kwargs):
        assert timeout == 0.01 and not check
        with subprocess.Popen(command, **kwargs) as child:
            try:
                ready, _, _ = select.select([ready_read], [], [], HANG_GUARD_SECONDS)
                assert ready and os.read(ready_read, 1) == b"R", "child did not report readiness"
                assert child.stdout is not None
                output = os.read(child.stdout.fileno(), len(b"suite-output-tail\n"))
                # Inject expiry only after the child has written its tail and blocked.
                raise subprocess.TimeoutExpired(command, timeout, output=output)
            finally:
                child.kill()
                child.wait(timeout=HANG_GUARD_SECONDS)

    # The test never writes the blocking pipe; readiness and the injected expiry
    # do not depend on the scheduler or elapsed wall-clock time.
    child = ("import os, sys; os.write(1, b'suite-output-tail\\n'); "
             "os.write(int(sys.argv[1]), b'R'); os.read(int(sys.argv[2]), 1)")
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            run_command([sys.executable, "-c", child, str(ready_write), str(block_read), "path/to/suite"],
                        timeout=0.01, runner=gated_runner, pass_fds=(ready_write, block_read), text=True,
                        stderr=subprocess.STDOUT)
        assert "path/to/suite" in str(failure.value)
        assert "suite-output-tail" in str(failure.value)
    finally:
        for fd in (ready_read, ready_write, block_read, block_write):
            os.close(fd)
