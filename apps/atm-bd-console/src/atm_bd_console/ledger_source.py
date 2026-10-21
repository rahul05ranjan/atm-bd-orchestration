"""I/O adapter for the run ledger and QA/finding rows consumed by the tabs."""
from __future__ import annotations

import json
from typing import Any

from . import beads_source, command, runs
from .closed_cache import ClosedBeads
from .result import Err, Error, IoFailed, Ok, ParseFailed, Result
from .skill_loader import load_skill


class LedgerUnavailable(RuntimeError):
    """The ledger could not be located; carries the typed error of the command that failed."""

    def __init__(self, error: Error) -> None:
        super().__init__(error.kind)
        self.error = error


def ledger_error(failure: Exception, repo_root: str) -> Error:
    """The typed Error for any way reading the sanity ledger can fail."""
    if isinstance(failure, LedgerUnavailable):
        return failure.error
    fix = "repair the sanity ledger file under .sc/sanity-log, then press r"
    if isinstance(failure, OSError):
        return IoFailed("io_failed", str(failure.filename or repo_root), failure.strerror or str(failure), fix)
    if isinstance(failure, ValueError):
        return ParseFailed("parse_failed", "sanity ledger", str(failure), fix)
    return IoFailed("io_failed", repo_root, str(failure), fix)


def read_ledger(repo_root: str, phase: str) -> list[dict[str, Any]]:
    shared, _ = load_skill(repo_root, "atm-beads/scripts/sanity_shared.py")

    def run_git(argv, **kwargs):
        result = command.run(argv, "run the git command by hand in the repo root and repair what it reports, then re-read the ledger", cwd=kwargs.get("cwd"))
        if isinstance(result, Err):
            raise LedgerUnavailable(result.error)
        return result.value

    root = shared.primary_root(repo_root, run=run_git)
    path = shared.ledger_path(root, phase)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def sanity_runs(repo_root: str, phase: str) -> Result[list[runs.SanityRun]]:
    """The sanity ledger is a file: it never depends on a bd read."""
    try:
        return Ok(runs.sanity_runs(read_ledger(repo_root, phase)))
    except (OSError, ValueError, RuntimeError) as failure:
        return Err(ledger_error(failure, repo_root))


def open_quality(phase: str, open_rows: tuple[dict[str, Any], ...]) -> tuple[list[runs.QaRun], list[dict[str, Any]]]:
    """The QA runs and finding rows among the open beads of one complete bd read."""
    by_id = {row.get("id"): row for row in open_rows}
    qa_rows = []
    findings = []
    for row in open_rows:
        labels = row.get("labels") or []
        if f"phase-{phase}" not in labels:
            continue
        if "stage:qa" in labels:
            metadata = row.get("metadata") or {}
            checked_id = metadata.get("checked_bead") if isinstance(metadata, dict) else None
            qa_rows.append({**row, "checked_bead_data": by_id.get(checked_id, {})})
        elif "stage:finding" in labels:
            findings.append(row)
    return runs.qa_runs(qa_rows), findings


def _closed_stage(repo_root: str, phase: str, stage: str, closed: ClosedBeads) -> Result[list[dict[str, Any]]]:
    """The closed rows of one stage: listed once, then topped up with the ids seen open that have since closed."""
    cached = closed.stage_rows(phase, stage)
    if cached is None:
        listed = beads_source.closed_stage_rows(phase, stage, cwd=repo_root)
        if isinstance(listed, Err):
            return listed
        closed.set_stage_rows(phase, stage, listed.value)
        return listed
    vanished = closed.vanished_stage_ids(phase, stage)
    if vanished:
        shown = beads_source.show_rows(vanished, cwd=repo_root)
        if isinstance(shown, Err):
            return shown
        closed.add_stage_rows(phase, stage, shown.value)
    return Ok(closed.stage_rows(phase, stage) or [])


def closed_quality(repo_root: str, phase: str, closed: ClosedBeads) -> Result[tuple[list[runs.QaRun], list[dict[str, Any]]]]:
    """Load just the closed QA and finding stages when the user requests Quality."""
    qa = _closed_stage(repo_root, phase, "qa", closed)
    if isinstance(qa, Err):
        return qa
    findings = _closed_stage(repo_root, phase, "finding", closed)
    if isinstance(findings, Err):
        return findings
    ids = tuple(sorted({checked for row in qa.value
                        if isinstance((meta := row.get("metadata")), dict)
                        if isinstance((checked := meta.get("checked_bead")), str) and checked}))
    missing = closed.missing_rows(ids)
    if missing:
        fresh = beads_source.show_rows(missing, cwd=repo_root)
        if isinstance(fresh, Err):
            return fresh
        closed.store_rows(fresh.value)
        by_id = {row.get("id"): row for row in [*fresh.value, *closed.cached_rows(ids)]}
    else:
        by_id = {row.get("id"): row for row in closed.cached_rows(ids)}
    enriched = []
    for row in qa.value:
        metadata = row.get("metadata")
        checked_id = metadata.get("checked_bead") if isinstance(metadata, dict) else None
        enriched.append({**row, "checked_bead_data": by_id.get(checked_id, {})})
    return Ok((runs.qa_runs(enriched), findings.value))
