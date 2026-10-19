from __future__ import annotations

import json
from pathlib import Path

import pytest
import sc_compose

from atm_bd_console import beads_source, command, runs
from atm_bd_console.result import Ok

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "plugins/atm-bd-orchestration/skills/atm-bd-orchestration"


def test_run_beads_reads_default_and_explicit_closed_without_all(monkeypatch) -> None:
    calls = []

    def run(argv, _fix, **_kwargs):
        calls.append(argv)
        return Ok(command.Output(0, json.dumps([{"id": "same"}, {"id": "closed", "status": "closed"}]
                                               if "--status" in argv else [{"id": "same", "status": "open"}])))

    monkeypatch.setattr(beads_source.command, "run", run)
    result = beads_source.run_beads("bj", "qa")
    assert isinstance(result, Ok) and [row["id"] for row in result.value] == ["same", "closed"]
    assert calls == [["bd", "list", "-l", "phase-bj,stage:qa", "-n", "0", "--json"],
                     ["bd", "list", "-l", "phase-bj,stage:qa", "--status", "closed", "-n", "0", "--json"]]


def test_sanity_merges_reviewers_newest_first_and_accepts_old_rows() -> None:
    rows = [
        {"run_id": "old", "reviewer": "sanity-llm", "completed_at": "2026-10-01T01:00:00Z", "sprint": "bj-1"},
        {"run_id": "new", "reviewer": "sanity-jev", "completed_at": "2026-10-02T01:00:00Z", "sprint": "bj-2",
         "kind": "API_FAILURE", "error": {"message": "verbatim"}, "dev": "alice"},
        {"run_id": "new", "reviewer": "sanity-llm", "completed_at": "2026-10-02T01:01:00Z", "sprint": "bj-2"},
    ]
    grouped = runs.sanity_runs(rows)
    assert [r.run_id for r in grouped] == ["new", "old"]
    assert grouped[0].dev == "alice" and grouped[0].jev == rows[1] and grouped[0].llm == rows[2]
    assert grouped[1].jev is None


def test_quality_uses_real_bead_fields_and_discovered_from_only() -> None:
    qa = {"id": "qa-1", "status": "closed", "started_at": "2026-10-02T00:46:00Z",
          "closed_at": "2026-10-02T01:00:00Z", "close_reason": "FAIL: found bug",
          "metadata": {"sprint": "bj-1", "round": 2}, "checked_bead_data": {"metadata": {"pr_number": 44}}}
    finding = {"id": "f1", "description": "issue\n\n## Evidence\napp.py:12 and src/thing.py:9", "design": "fix", "metadata": {"severity": "important", "reviewer": "req-qa"},
               "dependencies": [{"type": "discovered-from", "depends_on_id": "qa-1"}]}
    other = {**finding, "id": "f2", "dependencies": [{"type": "parent-child", "depends_on_id": "qa-1"}]}
    run = runs.qa_runs([qa])[0]
    assert (run.verdict, run.pr_number, run.round, run.elapsed_s) == ("FAIL", 44, 2, 840.0)
    selected = runs.run_findings(qa, [finding, other])
    assert [f["id"] for f in selected] == ["f1"]
    assert selected[0]["reviewer"] == "req-qa" and selected[0]["required_correction"] == "fix"
    assert selected[0]["refs"] == ["app.py:12", "src/thing.py:9"]


@pytest.mark.parametrize(("started_at", "closed_at", "expected"), [
    ("2026-10-02T00:46:00Z", "2026-10-02T01:00:00Z", 840.0),
    (None, "2026-10-02T01:00:00Z", None),
    ("invalid", "2026-10-02T01:00:00Z", None),
    ("2026-10-02T01:00:00Z", "2026-10-02T00:46:00Z", None),
    ("2026-10-02T00:46:00", "2026-10-02T01:00:00", None),
])
def test_quality_elapsed_requires_valid_ordered_timezone_aware_timestamps(started_at, closed_at, expected) -> None:
    run = runs.qa_runs([{"id": "qa-1", "status": "closed", "started_at": started_at,
                         "closed_at": closed_at, "close_reason": "PASS"}])[0]
    assert run.elapsed_s == expected


def test_quality_fixture_from_real_bead_templates() -> None:
    def rendered(name: str) -> dict:
        template = SKILL / f"templates/{name}.json.j2"
        values = json.loads((SKILL / f"examples/{name}-vars.json").read_text())
        request = sc_compose.ComposeRequest(str(template.parent), sc_compose.ComposeMode.file(str(template)),
                                           vars_input=values, policy=sc_compose.ComposePolicy(strict_undeclared_variables=True))
        return json.loads(sc_compose.compose_file(request).rendered_text)

    qa, finding = rendered("qa-bead"), rendered("finding-bead")
    qa.update(status="closed", closed_at="2026-10-02T01:00:00Z", close_reason="FAIL: two findings")
    second = {**finding, "id": "second", "metadata": {**finding["metadata"], "severity": "minor"}}
    assert runs.qa_runs([qa])[0].verdict == "FAIL"
    linked = runs.run_findings(qa, [finding, second])
    assert [row["severity"] for row in linked] == ["important", "minor"]


def test_qa_findings_template_is_strict() -> None:
    values = json.loads((SKILL / "examples/qa-findings-vars.json").read_text())
    rendered = runs.render_findings(str(ROOT), values["findings"])
    assert "example-f1" in rendered and "src/example.py:42" in rendered and "req-qa" in rendered
    template = SKILL / "templates/qa-findings.md.j2"
    request = sc_compose.ComposeRequest(str(template.parent), sc_compose.ComposeMode.file(str(template)),
                                       vars_input={}, policy=sc_compose.ComposePolicy(strict_undeclared_variables=True))
    with pytest.raises(sc_compose.ScComposeError):
        sc_compose.compose_file(request)
