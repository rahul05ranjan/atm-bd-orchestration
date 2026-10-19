from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sc_compose

from atm_bd_console import queues
from atm_bd_console.command import Output
from atm_bd_console.result import CommandFailed, Err, Ok

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
ROOT = "atm-phase-bj"


def bead(ident: str, stage: str, *, status: str = "open", parent: str | None = None, **extra):
    return {"id": ident, "title": ident, "status": status, "labels": [f"stage:{stage}"], "parent": parent,
            "priority": 2, "created_at": "2026-10-19T11:00:00Z", "updated_at": "2026-10-19T11:00:00Z", **extra}


def fixture_rows():
    rows = [
        bead(ROOT, "root", metadata={"integration_branch": "integrate/phase-bj"}),
        bead("sprint-empty", "sprint", parent=ROOT),
        bead("dev-flight", "dev", parent=ROOT),
        bead("dev-stalled", "dev", parent=ROOT),
        bead("dev-ready", "dev", parent=ROOT, metadata={"difficulty": "hard"}),
        bead("plan-review", "plan-review", parent=ROOT),
        bead("sanity-ready", "dev-sanity", parent=ROOT, metadata={"checked_bead": "dev-closed"}),
        bead("qa-ready", "qa", parent=ROOT),
        bead("qa-quick", "qa", parent=ROOT, metadata={"quick_fix": True}),
        bead("fix-ready", "fix", parent=ROOT, metadata={"finding_ref": "F-9", "round": 2, "filed_by": "qa"}),
        bead("finding-minor", "finding", parent=ROOT, metadata={"severity": "minor", "reviewer": "qa"}),
        bead("finding-important", "finding", parent=ROOT, metadata={"severity": "important", "reviewer": "qa"}),
        bead("other-ready", "other", parent=ROOT),
        bead("dev-parent", "dev", parent=ROOT),
        bead("sanity-finding", "finding", parent="dev-parent", metadata={"sanity_finding": True}),
        bead("claimed", "dev", status="in_progress", parent=ROOT, started_at="2026-10-19T10:00:00Z"),
        bead("prereq", "dev", parent=ROOT),
        bead("blocked", "dev", parent=ROOT, dependencies=[{"issue_id": "blocked", "depends_on_id": "prereq", "type": "blocks"}]),
        bead("waiting", "dev", parent=ROOT),
    ]
    return rows


def task(ident: str, *, state: str = "active", reminders: int = 0):
    return {"task_id": ident, "assignee": "dev", "state": state, "reminder_count": reminders,
            "assigned_at": "2026-10-19T10:00:00Z"}


def fixed_runner(open_rows=None, ready_rows=None, task_rows=None, closed_rows=None):
    open_rows = fixture_rows() if open_rows is None else open_rows
    ready_rows = ready_rows if ready_rows is not None else [
        row for row in open_rows if row["id"] in {"dev-ready", "plan-review", "sanity-ready", "qa-ready", "qa-quick", "fix-ready", "finding-minor", "finding-important", "other-ready"}
    ]
    task_rows = task_rows if task_rows is not None else [task("dev-flight"), task("dev-stalled", reminders=3), task("dev-closed")]
    closed_rows = closed_rows if closed_rows is not None else [bead("dev-closed", "dev", status="closed", closed_at="2026-10-19T11:30:00Z")]
    calls = []
    def run(argv, fix, *, cwd=None, **kwargs):
        calls.append((list(argv), cwd))
        if argv[:3] == ["bd", "--readonly", "list"]:
            rows = open_rows
        elif argv[:2] == ["bd", "ready"]:
            rows = ready_rows
        elif argv[:4] == ["atm", "task", "list", "--all"]:
            rows = task_rows
        elif argv[:5] == ["bd", "list", "--status", "closed", "--id"]:
            rows = closed_rows
        else:
            raise AssertionError(f"unexpected source command: {argv}")
        import json
        return Ok(Output(0, json.dumps(rows)))
    return run, calls


def report(*, ready_rows=None):
    runner, calls = fixed_runner(ready_rows=ready_rows)
    args = SimpleNamespace(phase=None, root=ROOT, stack=False)
    result = queues.build(args, Path("/fixture/product"), now=NOW, runner=runner)
    return result, calls


def test_build_classifies_each_queue_from_fixed_json_and_uses_four_batched_reads():
    result, calls = report()
    q = result["queues"]
    assert [row["id"] for row in q["in_flight"]] == ["dev-flight"]
    assert [row["id"] for row in q["task_stalled"]] == ["dev-stalled"]
    assert [row["id"] for row in q["task_bead_closed"]] == ["dev-closed"]
    assert [row["id"] for row in q["claimed_no_task"]] == ["claimed"]
    assert [row["id"] for row in q["container_closable"]] == ["sprint-empty"]
    assert [row["id"] for row in q["needs_plan_review"]] == ["plan-review"]
    assert [row["id"] for row in q["needs_dev"]] == ["dev-ready"]
    assert [row["id"] for row in q["needs_sanity"]] == ["sanity-ready"]
    assert {row["id"] for row in q["needs_qa"]} == {"qa-ready", "qa-quick"}
    assert [row["id"] for row in q["needs_fix"]] == ["fix-ready"]
    assert [row["id"] for row in q["needs_dev_fix"]] == ["dev-parent"]
    assert [row["id"] for row in q["minor_backlog"]] == ["finding-minor"]
    assert [row["id"] for row in q["needs_finding_fix"]] == ["finding-important"]
    assert [row["id"] for row in q["ready_other"]] == ["other-ready"]
    assert [row["id"] for row in q["not_ready"]] == ["blocked", "prereq", "waiting"]
    assert len(calls) == 4
    assert calls[0][0] == ["bd", "--readonly", "list", "-l", "phase-bj", "--exclude-label", "anchor", "-n", "0", "--json"]
    assert calls[1][0] == ["bd", "ready", "-l", "phase-bj", "--exclude-label", "anchor", "-n", "0", "--json"]
    assert calls[2][0] == ["atm", "task", "list", "--all", "--json"]
    assert calls[3][0] == ["bd", "list", "--status", "closed", "--id", "dev-closed", "--json"]
    assert all(cwd == Path("/fixture/product") for _, cwd in calls)


@pytest.mark.parametrize("status,ready,task_state", [
    ("open", True, None),
    ("open", False, None),
    ("in_progress", False, None),
    ("in_progress", False, "active"),
    ("closed", False, "active"),
])
def test_rendered_stack_ledger_never_appears_in_work_queues(status, ready, task_state):
    template = (Path(__file__).resolve().parents[3] / "plugins/atm-bd-orchestration/skills/atm-beads"
                / "templates/stack-bead.json.j2")
    request = sc_compose.ComposeRequest(str(template.parent), sc_compose.ComposeMode.file(str(template)),
                                       vars_input={"id": ROOT, "phase": "bj", "integration_branch": "integrate/phase-bj"},
                                       policy=sc_compose.ComposePolicy(strict_undeclared_variables=True))
    ledger = json.loads(sc_compose.compose_file(request).rendered_text)
    ledger["status"] = status
    root = bead(ROOT, "root", metadata={"integration_branch": "integrate/phase-bj"})
    work = bead("ordinary-work", "dev", parent=ROOT)
    open_rows = [root, work] + ([] if status == "closed" else [ledger])
    ready_rows = [work] + ([ledger] if ready else [])
    task_rows = [task(ledger["id"], state=task_state)] if task_state else []
    run, _ = fixed_runner(open_rows=open_rows, ready_rows=ready_rows, task_rows=task_rows,
                          closed_rows=[ledger] if status == "closed" else [])
    result = queues.build(SimpleNamespace(phase=None, root=ROOT, stack=False), Path("/fixture"), now=NOW, runner=run)

    assert [row["id"] for row in result["queues"]["needs_dev"]] == ["ordinary-work"]
    assert all(row["id"] != ledger["id"] for rows in result["queues"].values() for row in rows)


def test_all_mode_exposes_not_ready_reasons():
    result, _ = report()
    hidden = queues.render(result, False)
    shown = queues.render(result, True)
    assert "blocked" not in hidden
    assert "3 not-ready beads hidden" in hidden
    assert "== Not ready (prerequisite open) (3)" in shown
    assert "blocked by prereq" in shown


def test_cron_is_edge_triggered_and_rearms_after_a_row_leaves(tmp_path: Path):
    result, _ = report()
    state = tmp_path / "queues.json"
    first = queues.cron(result, state, 0, now=NOW)
    assert first is not None and {row["id"] for row in first["rows"]} >= {"dev-ready", "fix-ready"}
    assert queues.cron(result, state, 0, now=NOW) is None

    empty = {**result, "queues": {key: [] for key in result["queues"]}}
    assert queues.cron(empty, state, 0, now=NOW) is None
    assert queues.cron(result, state, 0, now=NOW) is not None


def test_failed_source_call_is_one_line_error_and_emits_no_partial_view(capsys):
    def failed(argv, fix, *, cwd=None, **kwargs):
        return Err(CommandFailed("command_failed", tuple(argv), 1, "first line\nlast failure", fix))
    args = SimpleNamespace(phase=None, root=ROOT, stack=False, json=False, all=False, min_age=10, state_file=None)

    assert queues.main(args, repo_root=Path("/fixture/product"), runner=failed, now=NOW) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "bd --readonly list -l phase-bj --exclude-label anchor" in captured.err
    assert ": last failure:" in captured.err


def test_no_closed_query_when_all_task_ids_are_open():
    open_rows = fixture_rows()
    tasks = [task("dev-flight")]
    run, calls = fixed_runner(open_rows=open_rows, task_rows=tasks, closed_rows=[])
    queues.build(SimpleNamespace(phase=None, root=ROOT, stack=False), Path("/fixture"), now=NOW, runner=run)
    assert len(calls) == 3


def test_phase_uses_console_phase_file_reader(monkeypatch, tmp_path: Path):
    run, _ = fixed_runner()
    calls = []
    phase_path = tmp_path / ".atm-bd" / "phase-bj.toml"
    phase_path.parent.mkdir(parents=True, exist_ok=True)
    phase_path.write_text('plan = "docs/plans/phase-bj.jsonl"\nroot = "atm-phase-bj"\nintegration_branch = "integrate/phase-bj"\ncode_checkout = "../product"\n')
    monkeypatch.setattr(queues.atm_bd, "phase_file", lambda repo, phase: calls.append((repo, phase)) or Ok(
        SimpleNamespace(root_id=ROOT, integration_branch="integrate/phase-bj")))

    args = SimpleNamespace(phase="bj", root=None, stack=False)
    result = queues.build(args, tmp_path, now=NOW, runner=run)

    assert calls == [(str(tmp_path), "bj")]
    assert result["root"] == ROOT
    assert result["integration_branch"] == "integrate/phase-bj"
    assert queues._resolve(args, tmp_path)[2] == (tmp_path / "../product").resolve()


def test_stack_queues_use_the_code_checkout_and_report_stack_problems():
    calls = []
    def run(argv, fix, *, cwd=None, **kwargs):
        calls.append((list(argv), cwd))
        import json
        if argv == ["git", "worktree", "list", "--porcelain"]:
            output = "worktree /fixture/code/integrate\nbranch refs/heads/integrate/phase-bj\n"
        elif argv[:2] == ["gh", "api"]:
            output = json.dumps([[{"number": 353, "open": True, "base": {"ref": "integrate/phase-bj"},
                                  "pull_requests": [{"number": 100, "state": "open", "head": {"ref": "layer-a"}}]}]])
        elif argv == ["gh", "stack", "view", "--json"]:
            output = json.dumps({"trunk": "integrate/phase-bj", "branches": [
                {"name": "integrate/phase-bj", "isMerged": False},
                {"name": "layer-b", "isMerged": False, "needsRebase": True,
                 "pr": {"number": 101}},
            ]})
        else:
            raise AssertionError(f"unexpected command {argv}")
        return Ok(Output(0, output))
    queues_by_key = {key: [] for key, _, _ in queues.QUEUES}

    note = queues.stacks("integrate/phase-bj", queues_by_key, NOW, Path("/fixture/code"), runner=run)

    assert note == "1 open stack(s) on integrate/phase-bj, 1 unmerged layer(s) in the local stack"
    assert [row["id"] for row in queues_by_key["stack_incoherent"]] == ["stack#353"]
    assert [row["id"] for row in queues_by_key["pr_unlinked"]] == ["pr#101"]
    assert calls[0][0][:2] == ["gh", "api"] and calls[0][1] == Path("/fixture/code")
    assert calls[1] == (["git", "worktree", "list", "--porcelain"], Path("/fixture/code"))
    assert calls[2] == (["gh", "stack", "view", "--json"], "/fixture/code/integrate")

