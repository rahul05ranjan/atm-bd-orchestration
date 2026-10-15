from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

# Only the system directories a fake script or a source's own subprocess needs; never the caller's PATH.
SYSTEM_PATH = ("/usr/bin", "/bin")
_UNIT = "\x1f"  # separates argv items in the log
_RECORD = "\x1e"  # ends one invocation


@dataclass(frozen=True)
class FakeBin:
    """Fake executables on a controlled PATH; each invocation's argv is appended to `log`."""

    dir: Path
    log: Path

    def make(self, name: str, script: str) -> Path:
        executable = self.dir / name
        executable.write_text(
            "#!/bin/sh\n"
            f"printf '%s{_UNIT}' \"$(basename \"$0\")\" \"$@\" >> \"$FAKE_ARGV_LOG\"\n"
            f"printf '{_RECORD}' >> \"$FAKE_ARGV_LOG\"\n" + script
        )
        executable.chmod(0o755)
        return executable

    def argv(self) -> list[tuple[str, ...]]:
        """One tuple per invocation, in call order: (program name, *arguments)."""
        if not self.log.exists():
            return []
        records = self.log.read_text().split(_RECORD)[:-1]
        return [tuple(record.split(_UNIT)[:-1]) for record in records]


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBin:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(log))
    monkeypatch.setenv("PATH", ":".join((str(bin_dir), *SYSTEM_PATH)))
    return FakeBin(bin_dir, log)
