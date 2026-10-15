from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from atm_bd_console.codec import from_json
from atm_bd_console.result import CommandFailed, NotFound, Ok, Timeout
from atm_bd_console.status import status_text
from atm_bd_console.types import Snapshot, SprintView, StepView

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    sprints = (
        SprintView("atm-bh-1", "contract", "closed", (), ()),
        SprintView("atm-bh-2", "source", "in_progress", ("atm-bh-1",),
                   (StepView("atm-bh-2.group-dev", "dev", "in_progress", "fenix"),)),
        SprintView("atm-bh-3", "later", "not_ready", ("atm-bh-1", "atm-bh-2"), ()),
    )
    return replace(decoded.value, mermaid="graph TD\n    a --> b", sprints=sprints)


def test_mmdflux_output_is_returned_verbatim(fake_bin, monkeypatch, tmp_path) -> None:
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    fake_bin("mmdflux", "cat >/dev/null\nprintf 'rendered\\n'\n")
    rendered, error = status_text(snapshot())
    assert rendered == "rendered\n" and error is None
    assert log.read_text().splitlines() == ["--format", "text"]


def test_missing_binary_uses_dependency_depth_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "")
    rendered, error = status_text(snapshot())
    assert isinstance(error, NotFound)
    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")


def test_nonzero_and_timeout_return_error_and_fallback(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "bad"))
    rendered, error = status_text(snapshot())
    assert isinstance(error, CommandFailed) and error.code == 1
    assert rendered.startswith("✅ bh-1")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(subprocess, "run", timed_out)
    rendered, error = status_text(snapshot())
    assert isinstance(error, Timeout) and error.seconds == 20
    assert rendered.startswith("✅ bh-1")
