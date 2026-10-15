"""The legacy bead-queues command is a transparent console snapshot viewer."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "bead-queues"


def run_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, output: str, status: int) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    argv = tmp_path / "argv"
    console = fake_bin / "atm-bd-console"
    console.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$FAKE_CONSOLE_ARGV"\n'
        'printf "%s" "$FAKE_CONSOLE_OUTPUT"\n'
        'exit "$FAKE_CONSOLE_STATUS"\n'
    )
    console.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CONSOLE_ARGV", str(argv))
    monkeypatch.setenv("FAKE_CONSOLE_OUTPUT", output)
    monkeypatch.setenv("FAKE_CONSOLE_STATUS", str(status))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--phase", "bh"],
        text=True,
        capture_output=True,
        check=False,
    )
    result.console_argv = argv.read_text().splitlines()  # type: ignore[attr-defined]
    return result


@pytest.mark.parametrize(("output", "status"), [("snapshot\\n", 0), ("console failure\\n", 2)])
def test_viewer_preserves_console_stdout_exit_code_and_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, status: int) -> None:
    result = run_viewer(tmp_path, monkeypatch, output=output, status=status)

    assert result.stdout == output
    assert result.returncode == status
    assert result.stderr == ""
    assert result.console_argv == ["show", "--phase", "bh"]  # type: ignore[attr-defined]


def test_viewer_explains_how_to_install_the_console_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--phase", "bh"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "atm-bd-console is not on PATH" in result.stderr
    assert "uv tool install" in result.stderr
    assert "apps/atm-bd-console" in result.stderr


def test_viewer_contains_no_beads_queue_queries() -> None:
    text = SCRIPT.read_text()

    assert "bd list" not in text
    assert "bd ready" not in text
    assert "bd show" not in text
    assert "-" + "-json" not in text
    assert "state" + "-file" not in text
