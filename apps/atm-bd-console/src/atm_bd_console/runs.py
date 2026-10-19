"""Read and combine the run-level facts written by the sanity and QA producers."""
from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import beads_source, command
from .result import Err


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
    verdict: Any
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
        reason = bead.get("close_reason")
        verdict = ("running" if bead.get("status") != "closed" else
                   next((v for v in ("PASS", "FAIL") if isinstance(reason, str) and reason.startswith(v)), None))
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


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def collect(repo_root: str, phase: str) -> tuple[list[SanityRun], list[QaRun], list[dict[str, Any]]]:
    common = command.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         "locate sanity ledger", cwd=repo_root)
    if isinstance(common, Err):
        raise RuntimeError(f"cannot locate sanity ledger: {common.error}")  # noqa: TRY004
    ledger = Path(common.value.stdout.strip()).parent / ".sc" / "sanity-log" / f"phase-{phase}.jsonl"
    qa = beads_source.run_beads(phase, "qa", cwd=repo_root)
    findings = beads_source.run_beads(phase, "finding", cwd=repo_root)
    if isinstance(qa, Err) or isinstance(findings, Err):
        failure = qa if isinstance(qa, Err) else findings
        raise RuntimeError(f"cannot load QA beads: {failure.error}")  # type: ignore[union-attr]  # noqa: TRY004
    ids = tuple(sorted({str(m["checked_bead"]) for q in qa.value
                        if isinstance((m := q.get("metadata")), dict) and m.get("checked_bead")}))
    checked = beads_source.raw_show(ids, cwd=repo_root)
    if isinstance(checked, Err):
        raise RuntimeError(f"cannot load checked beads: {checked.error}")  # noqa: TRY004
    by_id = {item.get("id"): item for item in checked.value}
    for bead in qa.value:
        bead["checked_bead_data"] = by_id.get((bead.get("metadata") or {}).get("checked_bead"), {})
    return sanity_runs(read_ledger(ledger)), qa_runs(qa.value), findings.value


def render_findings(repo_root: str, findings: list[dict[str, Any]]) -> str:
    root = Path(repo_root)
    installed = root / ".claude" / "skills"
    source = root / "plugins" / "atm-bd-orchestration" / "skills"
    bundled = Path(__file__).resolve().parent / "_skills" / "skills"
    skills = next((path for path in (installed, source, bundled)
                   if (path / "atm-beads/scripts/sc_render.py").is_file()
                   and (path / "atm-bd-orchestration/templates/qa-findings.md.j2").is_file()), None)
    if skills is None:
        raise RuntimeError("QA findings template or sc_render is not installed")
    spec = importlib.util.spec_from_file_location("atm_bd_sc_render", skills / "atm-beads/scripts/sc_render.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sc_render")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.render(skills / "atm-bd-orchestration/templates/qa-findings.md.j2", {"findings": findings}))
