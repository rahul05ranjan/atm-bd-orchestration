from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install an executable that records argv and returns scripted output."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")

    def make(name: str, script: str) -> Path:
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$FAKE_ARGV_LOG\"\n" + script)
        executable.chmod(0o755)
        return executable

    return make
