from __future__ import annotations

import hashlib
import re
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "live_gate"
APP_ROOT = Path(__file__).parents[1]
TRANSCRIPT = FIXTURE / "phase-bh.md"
SOURCE_SHA = re.compile(r"source_sha: ([0-9a-f]{40})")
INSTALLED_VERSION = re.compile(r"installed_version: ([0-9]+\.[0-9]+\.[0-9]+)")
REFRESH_SHA = re.compile(r"provenance: source SHA ([0-9a-f]{40})")
REFRESH_VERSION = re.compile(r"provenance: installed version ([0-9]+\.[0-9]+\.[0-9]+)")
TRANSCRIPT_SHA = re.compile(r"Source SHA: `([0-9a-f]{40})`")
TRANSCRIPT_VERSION = re.compile(r"Installed version: `([^`]+)`")
PAYLOAD_LINE = "# ---- payload follows: verbatim tmux capture-pane -p output ----\n"
CURRENT_HEADING = "## Current run (REFRESH-03)"
HISTORICAL_HEADING = "## Previous revision (R11 capture, retained verbatim)"
R11_FILES = {"bh-rdy-f11-dispatch.txt", "bh-rdy-f11-prs.txt", "bh-rdy-f11-status.txt"}
REFRESH_FILES = {"bh-refresh3-dispatch.txt", "bh-refresh3-prs.txt", "bh-refresh3-status.txt"}
CFGFPROOF = FIXTURE / "cfgproof-20261016-233500-7192e36d"
CFGFPROOF_TEMPLATE_SHA = "efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb"
CFGFPROOF_REPORT_SHA = "eaa678ebfe2d378c390952a28e6fdec5b29a3c5654ff9e10c64502369c8f984b"
LIVE_DISPATCH_CAVEAT = "not proven: live daemon dispatch"


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    assert match is not None, pattern.pattern
    return match.group(1)


def _sections() -> tuple[str, str]:
    text = TRANSCRIPT.read_text(encoding="utf-8")
    current = text[text.index(CURRENT_HEADING) : text.index(HISTORICAL_HEADING)]
    historical = text[text.index(HISTORICAL_HEADING) :]
    return current, historical


def _raw(names: set[str]) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted((FIXTURE / "raw").glob("bh-*.txt")) if path.name in names}


def _payload(raw: str) -> str:
    _, marker, body = raw.partition(PAYLOAD_LINE)
    assert marker, "payload marker missing"
    return body


def _fenced(payload: str) -> str:
    return "```text\n" + payload.rstrip("\n") + "\n```"


def _refresh_mismatches(section: str, raws: dict[str, str]) -> list[str]:
    sha = _first(TRANSCRIPT_SHA, section)
    version = _first(TRANSCRIPT_VERSION, section)
    mismatches = []
    for name, raw in raws.items():
        if _first(REFRESH_SHA, raw) != sha:
            mismatches.append(f"{name}: source SHA")
        if _first(REFRESH_VERSION, raw) != version:
            mismatches.append(f"{name}: installed version")
    return mismatches


def test_live_gate_transcript_and_raw_captures_share_build_provenance() -> None:
    _, historical = _sections()
    sha = TRANSCRIPT_SHA.search(historical)
    version = TRANSCRIPT_VERSION.search(historical)

    assert sha is not None
    assert version is not None
    raw_files = sorted((FIXTURE / "raw").glob("bh-rdy-f11-*.txt"))
    assert {path.name for path in raw_files} == R11_FILES

    for path in raw_files:
        raw = path.read_text(encoding="utf-8")
        raw_sha = SOURCE_SHA.search(raw)
        raw_version = INSTALLED_VERSION.search(raw)
        assert raw_sha is not None, path.name
        assert raw_version is not None, path.name
        assert raw_sha.group(1) == sha.group(1), path.name
        assert raw_version.group(1) == version.group(1), path.name


def test_refresh3_transcript_and_raw_captures_share_build_provenance() -> None:
    current, _ = _sections()
    raws = _raw(REFRESH_FILES)

    assert set(raws) == REFRESH_FILES
    assert _refresh_mismatches(current, raws) == []


def test_refresh3_payloads_match_their_markdown_blocks_and_links() -> None:
    current, _ = _sections()
    raws = _raw(REFRESH_FILES)

    for name, raw in raws.items():
        assert _fenced(_payload(raw)) in current, name
        assert f"raw/{name}" in current, name


def test_a_single_mismatched_refresh3_header_is_reported() -> None:
    current, _ = _sections()
    raws = _raw(REFRESH_FILES)
    sha = _first(REFRESH_SHA, raws["bh-refresh3-dispatch.txt"])
    tampered = dict(raws)
    tampered["bh-refresh3-dispatch.txt"] = raws["bh-refresh3-dispatch.txt"].replace(sha, "d22382d" + "0" * 33, 1)

    assert tampered != raws
    assert _refresh_mismatches(current, tampered) == ["bh-refresh3-dispatch.txt: source SHA"]


def test_a_changed_refresh3_payload_no_longer_matches_its_block() -> None:
    current, _ = _sections()
    raw = _raw(REFRESH_FILES)["bh-refresh3-status.txt"]
    changed = raw.replace(PAYLOAD_LINE, PAYLOAD_LINE + "tampered line\n", 1)

    assert changed != raw
    assert _fenced(_payload(changed)) not in current


def _cfgproof_provenance() -> list[tuple[str, Path, str]]:
    entries = []
    for line in (CFGFPROOF / "PROVENANCE.md").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            entries.append((parts[0], CFGFPROOF / parts[1], parts[2]))
    return entries


def test_cfgproof_live_dispatch_evidence_has_byte_provenance() -> None:
    entries = _cfgproof_provenance()

    assert len(entries) == 17
    for expected_sha, path, tar_entry in entries:
        assert path.is_file(), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha, path
        assert tar_entry

    report = (CFGFPROOF / "REPORT.md").read_text(encoding="utf-8")
    catalog = "### Catalog Observation"
    search = "### Correlated Search"
    mailbox = "### Receiver mailbox read"
    assert hashlib.sha256((CFGFPROOF / "REPORT.md").read_bytes()).hexdigest() == CFGFPROOF_REPORT_SHA
    assert CFGFPROOF_TEMPLATE_SHA in report
    assert all(marker in report for marker in (catalog, search, mailbox, "### Commit correlation"))


def test_live_dispatch_caveat_is_replaced_by_cfgproof_evidence() -> None:
    for path in (
        APP_ROOT / "CONTRACT.md",
        APP_ROOT / "README.md",
        FIXTURE / "phase-bh.md",
        FIXTURE / "cfgproof-20261016-233500-7192e36d" / "REPORT.md",
    ):
        assert LIVE_DISPATCH_CAVEAT not in path.read_text(encoding="utf-8"), path
