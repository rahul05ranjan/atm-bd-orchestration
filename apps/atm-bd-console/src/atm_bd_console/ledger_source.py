"""I/O adapter for the run ledger and QA/finding rows consumed by the tabs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from . import beads_source, runs
from .result import Err


def _ledger_path_helper(repo_root: str) -> Any:
    root = Path(repo_root)
    candidates = (
        root / ".claude/skills/atm-beads/scripts/sanity_shared.py",
        root / "plugins/atm-bd-orchestration/skills/atm-beads/scripts/sanity_shared.py",
        Path(__file__).resolve().parents[4] / "plugins/atm-bd-orchestration/skills/atm-beads/scripts/sanity_shared.py",
        Path(__file__).resolve().parent / "_skills/skills/atm-beads/scripts/sanity_shared.py",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError("sanity ledger path helper is not installed")
    spec = importlib.util.spec_from_file_location("atm_bd_sanity_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sanity ledger path helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_ledger(repo_root: str, phase: str) -> list[dict[str, Any]]:
    shared = _ledger_path_helper(repo_root)
    common = beads_source.command.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                      "locate sanity ledger", cwd=repo_root)
    if isinstance(common, Err):
        raise RuntimeError(f"cannot locate sanity ledger: {common.error}")  # noqa: TRY004
    root = Path(common.value.stdout.strip()).parent
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
