"""Read and combine the run-level facts written by the sanity and QA producers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .enums import QaState
from .model import qa_state_from_close


def _when(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=UTC)


def _elapsed_s(started_at: Any, closed_at: Any) -> float | None:
    """Return a recorded QA run's elapsed time when both UTC timestamps are valid."""
    if not isinstance(started_at, str) or not isinstance(closed_at, str):
        return None
    try:
        started = datetime.fromisoformat(started_at)
        closed = datetime.fromisoformat(closed_at)
    except ValueError:
        return None
    if started.tzinfo is None or closed.tzinfo is None:
        return None
    elapsed_s = (closed.astimezone(UTC) - started.astimezone(UTC)).total_seconds()
    return elapsed_s if elapsed_s >= 0 else None


@dataclass(frozen=True)
class SanityRun:
    run_id: str
    completed_at: str
    sprint: str
    pr_number: Any
    dev: str | None
    iteration: Any
    final_verdict: str
    jev: dict[str, Any] | None
    llm: dict[str, Any] | None


@dataclass(frozen=True)
class QaRun:
    bead: dict[str, Any]
    completed_at: str
    sprint: str
    pr_number: Any
    round: Any
    verdict: QaState | None
    elapsed_s: Any


def sanity_runs(rows: list[dict[str, Any]]) -> list[SanityRun]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and row.get("reviewer") in ("sanity-jev", "sanity-llm"):
            grouped.setdefault(run_id, []).append(row)
    result = []
    for run_id, group in grouped.items():
        latest = max(group, key=lambda row: _when(row.get("completed_at")))
        reviewers = {row["reviewer"]: row for row in group}
        result.append(SanityRun(run_id, str(latest.get("completed_at") or ""), str(latest.get("sprint") or ""),
                                latest.get("pr_number"), next((r.get("dev") for r in group if r.get("dev")), None),
                                latest.get("iteration"), str(latest.get("final_verdict") or ""),
                                reviewers.get("sanity-jev"), reviewers.get("sanity-llm")))
    return sorted(result, key=lambda run: _when(run.completed_at), reverse=True)


def qa_runs(qa_beads: list[dict[str, Any]]) -> list[QaRun]:
    result = []
    for bead in qa_beads:
        metadata = bead.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        verdict = (QaState.RUNNING if bead.get("status") != "closed" else qa_state_from_close(bead.get("close_reason")))
        checked = bead.get("checked_bead_data") or {}
        checked_metadata = checked.get("metadata") or {} if isinstance(checked, dict) else {}
        result.append(QaRun(bead, str(bead.get("closed_at") or ""), str(metadata.get("sprint") or ""),
                            checked_metadata.get("pr_number"), metadata.get("round"), verdict,
                            _elapsed_s(bead.get("started_at"), bead.get("closed_at"))))
    return sorted(result, key=lambda run: _when(run.completed_at), reverse=True)


def run_findings(qa_bead: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for finding in findings:
        if not any(isinstance(dep, dict) and dep.get("type") == "discovered-from"
                   and dep.get("depends_on_id", dep.get("id")) == qa_bead.get("id")
                   for dep in finding.get("dependencies", [])):
            continue
        metadata = finding.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        description = finding.get("description") or ""
        if not isinstance(description, str):
            description = ""
        evidence = description.partition("\n\n## Evidence\n")[2]
        refs = metadata.get("refs") or re.findall(r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+:\d+", evidence)
        result.append({"id": finding.get("id", ""), "severity": metadata.get("severity", ""),
                       "category": metadata.get("category", ""), "refs": refs,
                       "issue": description.partition("\n\n## Deliverables\n")[0], "required_correction": finding.get("design", ""),
                       "reviewer": metadata.get("reviewer", "")})
    return result
