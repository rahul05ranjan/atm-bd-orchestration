"""I/O adapter for the run ledger and QA/finding rows consumed by the tabs."""
from __future__ import annotations

import json
from typing import Any

from . import beads_source, command, runs
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


def closed_quality(repo_root: str, phase: str) -> Result[tuple[list[runs.QaRun], list[dict[str, Any]]]]:
    """Load just the closed QA and finding stages when the user requests Quality."""
    qa = beads_source.closed_stage_rows(phase, "qa", cwd=repo_root)
    if isinstance(qa, Err):
        return qa
    findings = beads_source.closed_stage_rows(phase, "finding", cwd=repo_root)
    if isinstance(findings, Err):
        return findings
    ids = tuple(sorted({checked for row in qa.value
                        if isinstance((meta := row.get("metadata")), dict)
                        if isinstance((checked := meta.get("checked_bead")), str) and checked}))
    checked = beads_source.show_rows(ids, cwd=repo_root)
    if isinstance(checked, Err):
        return checked
    by_id = {row.get("id"): row for row in checked.value}
    enriched = []
    for row in qa.value:
        metadata = row.get("metadata")
        checked_id = metadata.get("checked_bead") if isinstance(metadata, dict) else None
        enriched.append({**row, "checked_bead_data": by_id.get(checked_id, {})})
    return Ok((runs.qa_runs(enriched), findings.value))
