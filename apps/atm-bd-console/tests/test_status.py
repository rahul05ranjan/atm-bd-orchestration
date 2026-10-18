from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from atm_bd_console.codec import from_json
from atm_bd_console.enums import SprintState
from atm_bd_console.result import CommandFailed, ConfigInvalid, NotFound, Ok, Timeout
from atm_bd_console.status import status_text
from atm_bd_console.types import Snapshot, SprintView, StepView

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    sprints = (
        SprintView("atm-bh-1", "contract", SprintState.CLOSED, (), ()),
        SprintView("atm-bh-2", "source", SprintState.IN_PROGRESS, ("atm-bh-1",),
                   (StepView("atm-bh-2.group-dev", "dev", "in_progress", "fenix"),)),
        SprintView("atm-bh-3", "later", SprintState.NOT_READY, ("atm-bh-1", "atm-bh-2"), ()),
    )
    return replace(decoded.value, mermaid="graph TD\n    a --> b", sprints=sprints)


def test_mmdflux_output_is_returned_verbatim(fake_bin, monkeypatch, tmp_path) -> None:
    stdin_log = tmp_path / "mmdflux-stdin"
    monkeypatch.setenv("FAKE_STDIN_LOG", str(stdin_log))
    fake_bin.make(
        "mmdflux",
        'if [ "$1" = "--version" ]; then printf "mmdflux 2.6.1\\n"; exit 0; fi\n'
        'cat > "$FAKE_STDIN_LOG"\nprintf \'rendered\\n\'\n',
    )
    rendered, error = status_text(snapshot())
    assert rendered == "rendered\n" and error is None
    assert stdin_log.read_text() == "graph TD\n    a --> b"
    assert fake_bin.argv() == [("mmdflux", "--version"), ("mmdflux", "--format", "text")]


def test_old_mmdflux_returns_typed_fix_without_rendering(fake_bin) -> None:
    fake_bin.make("mmdflux", 'if [ "$1" = "--version" ]; then printf "mmdflux 2.6.0\\n"; exit 0; fi\n')

    rendered, error = status_text(snapshot())

    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")
    assert isinstance(error, ConfigInvalid)
    assert error.key == "mmdflux_version"
    assert error.value == "mmdflux 2.6.0"
    assert error.fix == "install or upgrade mmdflux >=2.6.1"
    assert fake_bin.argv() == [("mmdflux", "--version")]


def test_unversioned_mmdflux_returns_typed_fix_without_rendering(fake_bin) -> None:
    fake_bin.make("mmdflux", 'if [ "$1" = "--version" ]; then printf "unknown\\n"; exit 0; fi\n')

    _, error = status_text(snapshot())

    assert isinstance(error, ConfigInvalid)
    assert error.key == "mmdflux_version"
    assert error.fix == "install or upgrade mmdflux >=2.6.1"
    assert fake_bin.argv() == [("mmdflux", "--version")]


def test_missing_binary_uses_dependency_depth_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "")
    rendered, error = status_text(snapshot())
    assert isinstance(error, NotFound)
    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")


def test_fallback_is_one_row_per_sprint_in_dependency_depth_order(monkeypatch) -> None:
    sprints = (
        SprintView("atm-bh-3", "later", SprintState.NOT_READY, ("atm-bh-2",), ()),
        SprintView("atm-bh-2", "source", SprintState.IN_PROGRESS, ("atm-bh-1",),
                   (StepView("atm-bh-2.group-dev", "dev", "in_progress", "fenix"),)),
        SprintView("atm-bh-1", "contract", SprintState.CLOSED, (), ()),
        SprintView("atm-bh-4", "other", SprintState.READY, (),
                   (StepView("atm-bh-4.group-dev", "dev", "open", None),)),
    )
    value = replace(snapshot(), sprints=sprints)
    monkeypatch.setenv("PATH", "")
    rendered, error = status_text(value)
    assert isinstance(error, NotFound)
    # Depth 0 first (bh-1 before bh-4 by declared order), then depth 1, then depth 2.
    assert rendered == "\U000f05e0 bh-1\n\U000f040d bh-4  dev: unassigned\n\U000f051f bh-2  dev: ·fenix\n\U000f137c bh-3  ← bh-2"


def test_nonzero_and_timeout_return_error_and_fallback(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "bad"))
    rendered, error = status_text(snapshot())
    assert isinstance(error, CommandFailed) and error.code == 1
    assert rendered.startswith("\U000f05e0 bh-1")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(subprocess, "run", timed_out)
    rendered, error = status_text(snapshot())
    assert isinstance(error, Timeout) and error.seconds == 20
    assert rendered.startswith("\U000f05e0 bh-1")
