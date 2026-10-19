import subprocess

import pytest
from subprocess_helpers import COMMAND_TIMEOUT, OUTPUT_TAIL, run_command


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
