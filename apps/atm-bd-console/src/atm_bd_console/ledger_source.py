"""I/O adapter for the run ledger and QA/finding rows consumed by the tabs."""
from __future__ import annotations

import json
from typing import Any

from . import beads_source, command, runs
from .closed_cache import ClosedBeads
from .result import Err, Ok, Result
from .skill_loader import load_skill


def read_ledger(repo_root: str, phase: str) -> list[dict[str, Any]]:
    shared, _ = load_skill(repo_root, "atm-beads/scripts/sanity_shared.py")

    def run_git(argv, **kwargs):
        result = command.run(argv, "run the git command by hand in the repo root and repair what it reports, then re-read the ledger", cwd=kwargs.get("cwd"))
        if isinstance(result, Err):
            raise RuntimeError(f"cannot locate sanity ledger: {result.error}")  # noqa: TRY004
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


def collect(repo_root: str, phase: str, open_rows: tuple[dict[str, Any], ...], closed_rows: list[dict[str, Any]]) -> tuple[list[runs.SanityRun], list[runs.QaRun], list[dict[str, Any]]]:
    all_rows = [*open_rows, *closed_rows]
    by_id = {row.get("id"): row for row in all_rows}
    qa_rows = []
    findings = []
    for row in all_rows:
        labels = row.get("labels") or []
        if f"phase-{phase}" not in labels:
            continue
        if "stage:qa" in labels:
            metadata = row.get("metadata") or {}
            checked_id = metadata.get("checked_bead") if isinstance(metadata, dict) else None
            qa_rows.append({**row, "checked_bead_data": by_id.get(checked_id, {})})
        elif "stage:finding" in labels:
            findings.append(row)
    return runs.sanity_runs(read_ledger(repo_root, phase)), runs.qa_runs(qa_rows), findings


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
