from __future__ import annotations

import re
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "live_gate"
SOURCE_SHA = re.compile(r"source_sha: ([0-9a-f]{40})")
INSTALLED_VERSION = re.compile(r"installed_version: ([0-9]+\.[0-9]+\.[0-9]+)")


def test_live_gate_transcript_and_raw_captures_share_build_provenance() -> None:
    transcript = (FIXTURE / "phase-bh.md").read_text(encoding="utf-8")
    sha = re.search(r"Source SHA: `([0-9a-f]{40})`", transcript)
    version = re.search(r"Installed version: `([^`]+)`", transcript)

    assert sha is not None
    assert version is not None
    raw_files = sorted((FIXTURE / "raw").glob("bh-rdy-f11-*.txt"))
    assert {path.name for path in raw_files} == {
        "bh-rdy-f11-dispatch.txt",
        "bh-rdy-f11-prs.txt",
        "bh-rdy-f11-status.txt",
    }

    for path in raw_files:
        raw = path.read_text(encoding="utf-8")
        raw_sha = SOURCE_SHA.search(raw)
        raw_version = INSTALLED_VERSION.search(raw)
        assert raw_sha is not None, path.name
        assert raw_version is not None, path.name
        assert raw_sha.group(1) == sha.group(1), path.name
        assert raw_version.group(1) == version.group(1), path.name
