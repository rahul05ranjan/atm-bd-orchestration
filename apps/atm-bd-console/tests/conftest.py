from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from contract_fixtures import RUNNING
from textual.worker import WorkerCancelled

from atm_bd_console import app as console
from atm_bd_console.types import Config, Snapshot, State


@pytest.fixture
def phase_repo(tmp_path: Path) -> Path:
    root = tmp_path / "phase-repo"
    shutil.copytree(Path(__file__).parent / "fixtures/beads/repo", root)
    (root / ".atm.toml").write_text('[atm]\ndefault_team = "atm-dev"\n')
    return root

# Only the system directories a fake script or a source's own subprocess needs; never the caller's PATH.
SYSTEM_PATH = ("/usr/bin", "/bin")
_UNIT = "\x1f"  # separates argv items in the log
_RECORD = "\x1e"  # ends one invocation


class FakeStep:
    def __init__(self, snapshot: Snapshot = RUNNING) -> None:
        self.snapshot = snapshot
        self.calls: list[bool] = []

    def __call__(self, config: Config, state: State, force_github: bool) -> tuple[Snapshot, State]:
        self.calls.append(force_github)
        return self.snapshot, state


@pytest.fixture(autouse=True)
def no_mmdflux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console.status, "status_text", lambda snapshot: (snapshot.mermaid, None))


async def settle(pilot) -> None:
    try:
        await asyncio.wait_for(pilot.app.workers.wait_for_complete(), timeout=5)
    except WorkerCancelled:
        pass
    except TimeoutError as error:
        raise AssertionError("Textual workers did not complete within 5 seconds") from error
    await pilot.pause()


async def _bounded(coro):
    try:
        return await asyncio.wait_for(coro, timeout=5)
    except TimeoutError as error:
        raise AssertionError("Textual test scenario did not complete within 5 seconds") from error


def run_scenario(coro):
    return asyncio.run(_bounded(coro))


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
