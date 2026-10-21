from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sc_compose

from atm_bd_console import config, queues
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
        bead(f"{ROOT}.stack", "stack"),
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
        elif argv[:6] == ["bd", "list", "--status", "closed", "-n", "0"]:
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
    assert [row["id"] for row in q["not_ready"]] == ["blocked", "prereq", "sanity-finding", "waiting"]
    assert len(calls) == 4
    assert calls[0][0] == ["bd", "--readonly", "list", "-l", "phase-bj", "--exclude-label", "anchor", "-n", "0", "--json"]
    assert calls[1][0] == ["bd", "ready", "-l", "phase-bj", "--exclude-label", "anchor", "-n", "0", "--json"]
    assert calls[2][0] == ["atm", "task", "list", "--all", "--json"]
    assert calls[3][0] == ["bd", "list", "--status", "closed", "-n", "0", "--id", "dev-closed", "--json"]
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
    assert "bd not-ready: 3 dev | 1 fix" in hidden
    assert "== Not ready (prerequisite open) (4)" in shown
    assert "blocked by prereq" in shown
    assert "handled by dev-fix for dev-parent" in shown


def test_not_ready_summary_counts_stages_and_metadata_in_order():
    rows = [
        bead(ROOT, "root", metadata={"integration_branch": "integrate/phase-bj"}),
        bead("qa-name", "sprint", parent=ROOT),
        bead("one", "dev", parent="qa-name"),
        bead("two", "group", parent=ROOT),
        bead("three", "fix", parent=ROOT),
        bead("four", "finding", parent=ROOT),
        bead("five", "dev-sanity", parent=ROOT),
        bead("six", "qa", parent=ROOT),
        bead("seven", "plan-review", parent=ROOT),
        bead("eight", "review", parent=ROOT),
        bead("nine", "unknown", parent=ROOT, metadata={"template_kind": "qa"}),
        bead("ten", "unknown", parent=ROOT),
    ]
    runner, _ = fixed_runner(open_rows=rows, ready_rows=[], task_rows=[])
    result = queues.build(SimpleNamespace(phase=None, root=ROOT, stack=False), Path("/fixture"), now=NOW, runner=runner)

    assert "bd not-ready: 2 sprint | 1 dev | 2 fix | 1 sane | 2 qa | 2 review | 1 other" in queues.render(result, False).splitlines()
    shown = queues.render(result, True)
    assert all(row["id"] in shown and row["detail"] in shown for row in result["queues"]["not_ready"])


def test_not_ready_summary_omits_zero_kinds_and_classifies_unknown_as_other():
    rows = [bead(ROOT, "root"), bead("fix-in-id", "unknown", parent=ROOT)]
    runner, _ = fixed_runner(open_rows=rows, ready_rows=[], task_rows=[])
    result = queues.build(SimpleNamespace(phase=None, root=ROOT, stack=False), Path("/fixture"), now=NOW, runner=runner)

    assert "bd not-ready: 1 other" in queues.render(result, False).splitlines()


def test_sanity_finding_metadata_classifies_unlabelled_child_as_fix():
    rows = [bead(ROOT, "root"), bead("parent", "dev", parent=ROOT),
            bead("child", "unknown", parent="parent", metadata={"sanity_finding": True})]
    runner, _ = fixed_runner(open_rows=rows, ready_rows=[], task_rows=[])
    result = queues.build(SimpleNamespace(phase=None, root=ROOT, stack=False), Path("/fixture"), now=NOW, runner=runner)

    assert "bd not-ready: 1 fix" in queues.render(result, False).splitlines()
    assert result["queues"]["not_ready"][0]["detail"] == "handled by dev-fix for parent"


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
    code_root = tmp_path / "../product"
    def validate(root, checkout):
        calls.append((str(root), checkout))
        return Ok((str(code_root.resolve()), "owner/repo"))
    monkeypatch.setattr(config, "code_repository", validate)
    monkeypatch.setattr(queues.atm_bd, "phase_file", lambda repo, phase: Ok(
        SimpleNamespace(root_id=ROOT, code_checkout="../product")))

    args = SimpleNamespace(phase="bj", root=None, stack=False)
    result = queues.build(args, tmp_path, now=NOW, runner=run)

    assert calls == [(str(tmp_path), "../product")]
    assert result["root"] == ROOT
    assert result["integration_branch"] == "integrate/phase-bj"
    resolved = queues._resolve(args, tmp_path)
    assert resolved[2] == (tmp_path / "../product").resolve()
    assert resolved[3] == "owner/repo"


def test_phase_rejects_non_git_code_checkout_before_running_gh(monkeypatch, tmp_path: Path, capsys):
    (tmp_path / ".atm-bd").mkdir()
    (tmp_path / ".atm-bd" / "phase-bj.toml").write_text('integration_branch = "integrate/phase-bj"\n')
    calls = []
    def run(argv, fix, *, cwd=None, **kwargs):
        calls.append((list(argv), cwd))
        if tuple(argv) == ("git", "rev-parse", "--show-toplevel"):
            return Err(CommandFailed("command_failed", tuple(argv), 128, "not a git repository", fix))
        raise AssertionError(f"unexpected validation command: {argv}")
    monkeypatch.setattr(config.command, "run", run)
    monkeypatch.setattr(queues.atm_bd, "phase_file", lambda repo, phase: Ok(
        SimpleNamespace(root_id=ROOT, code_checkout="../not-a-repo")))
    args = SimpleNamespace(phase="bj", root=None, stack=True, json=False, all=False, min_age=10, state_file=None)

    assert queues.main(args, repo_root=tmp_path, runner=run, now=NOW) == 2
    captured = capsys.readouterr()
    # The original command error (argv, exit code, stderr) is printed, not a re-invented config_invalid.
    assert "git rev-parse" in captured.err and "[128] not a git repository" in captured.err
    assert calls == [(["git", "rev-parse", "--show-toplevel"], (tmp_path / "../not-a-repo").resolve())]
    assert not any(argv[0] == "gh" for argv, _ in calls)


def test_phase_rejects_code_checkout_subdirectory_with_primary_checkout_hint(monkeypatch, tmp_path: Path, capsys):
    (tmp_path / ".atm-bd").mkdir()
    (tmp_path / ".atm-bd" / "phase-bj.toml").write_text('integration_branch = "integrate/phase-bj"\n')
    checkout = (tmp_path / "../checkout").resolve()
    subdirectory = checkout / "subdir"
    calls = []
    def run(argv, fix, *, cwd=None, **kwargs):
        calls.append((list(argv), cwd))
        if tuple(argv) == ("git", "rev-parse", "--show-toplevel"):
            return Ok(Output(0, f"{checkout}\n"))
        raise AssertionError(f"unexpected validation command: {argv}")
    monkeypatch.setattr(config.command, "run", run)
    monkeypatch.setattr(queues.atm_bd, "phase_file", lambda repo, phase: Ok(
        SimpleNamespace(root_id=ROOT, code_checkout="../checkout/subdir")))
    args = SimpleNamespace(phase="bj", root=None, stack=True, json=False, all=False, min_age=10, state_file=None)

    assert queues.main(args, repo_root=tmp_path, runner=run, now=NOW) == 2
    captured = capsys.readouterr()
    assert f"use the primary checkout at {checkout}" in captured.err
    assert calls == [(["git", "rev-parse", "--show-toplevel"], subdirectory)]


def test_phase_stack_check_uses_valid_sibling_checkout(monkeypatch, tmp_path: Path):
    (tmp_path / ".atm-bd").mkdir()
    (tmp_path / ".atm-bd" / "phase-bj.toml").write_text('integration_branch = "integrate/phase-bj"\n')
    code_root = (tmp_path / "../code-checkout").resolve()
    validation_calls = []
    source_runner, _source_calls = fixed_runner()
    def run(argv, fix, *, cwd=None, **kwargs):
        validation_calls.append((list(argv), cwd))
        if tuple(argv) == ("git", "rev-parse", "--show-toplevel"):
            return Ok(Output(0, f"{code_root}\n"))
        if tuple(argv) == ("git", "remote", "get-url", "origin"):
            return Ok(Output(0, "https://github.com/owner/repo.git\n"))
        if tuple(argv) == ("git", "worktree", "list", "--porcelain"):
            return Ok(Output(0, f"worktree {code_root / 'integrate'}\nbranch refs/heads/integrate/phase-bj\n"))
        if tuple(argv) == ("gh", "stack", "view", "--json"):
            return Ok(Output(0, json.dumps({"trunk": "integrate/phase-bj", "branches": []})))
        return source_runner(list(argv), fix, cwd=cwd, **kwargs)
    monkeypatch.setattr(config.command, "run", run)
    monkeypatch.setattr(queues.atm_bd, "phase_file", lambda repo, phase: Ok(
        SimpleNamespace(root_id=ROOT, code_checkout="../code-checkout")))
    args = SimpleNamespace(phase="bj", root=None, stack=True)

    report = queues.build(args, tmp_path, now=NOW, runner=run)

    assert report["integration_branch"] == "integrate/phase-bj"
    assert not any(argv[:2] == ["gh", "api"] for argv, _ in validation_calls)
    assert (["git", "worktree", "list", "--porcelain"], code_root) in validation_calls
    assert ("git", "rev-parse", "--show-toplevel") in [tuple(argv) for argv, _ in validation_calls]


TRUNK = "integrate/phase-bj"
CODE = Path("/fixture/code")
WORKTREES = {TRUNK: "/fixture/code/integrate", "layer-a": "/fixture/code/a", "layer-b": "/fixture/code/b"}


def stack_runner(view=None, *, view_error=False, ancestors=None):
    """Fake the code checkout: `git worktree list`, one `gh stack view --json`, and `git merge-base --is-ancestor`."""
    calls = []
    def run(argv, fix, *, cwd=None, **kwargs):
        calls.append((list(argv), str(cwd)))
        if argv == ["git", "worktree", "list", "--porcelain"]:
            return Ok(Output(0, "".join(f"worktree {path}\nbranch refs/heads/{name}\n" for name, path in WORKTREES.items())))
        if argv == ["gh", "stack", "view", "--json"]:
            if view_error:
                return Err(CommandFailed("command_failed", tuple(argv), 1, "current branch is not part of a stack", fix))
            return Ok(Output(0, json.dumps(view)))
        if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
            return Ok(Output(0 if (argv[3], argv[4]) in (ancestors or set()) else 1, ""))
        raise AssertionError(f"unexpected command {argv}")
    return run, calls


def stack_view(*layers):
    return {"trunk": TRUNK, "currentBranch": TRUNK, "branches": [
        {"name": TRUNK, "head": "t" * 40, "base": "0" * 40, "isMerged": False, "isQueued": False, "needsRebase": False},
        *layers]}


def layer(name, number, head, base, **extra):
    return {"name": name, "head": head, "base": base, "isMerged": False, "isQueued": False, "needsRebase": False,
            "pr": {"number": number, "url": f"https://example.test/pull/{number}", "state": "OPEN"}, **extra}


def check(run, layers=("layer-a", "layer-b")):
    rows = {key: [] for key, _, _ in queues.QUEUES}
    note = queues.stacks(TRUNK, rows, NOW, CODE, run, layers)
    return note, rows


def test_properly_stacked_layers_produce_no_row_and_one_stack_view():
    view = stack_view(layer("layer-a", 1, "a" * 40, "t" * 40), layer("layer-b", 2, "b" * 40, "a" * 40))
    run, calls = stack_runner(view)

    note, rows = check(run)

    assert all(not value for value in rows.values())
    assert note == f"0 stack issue(s) on {TRUNK} (gh stack view)"
    assert [argv for argv, _ in calls].count(["gh", "stack", "view", "--json"]) == 1
    assert calls[1] == (["gh", "stack", "view", "--json"], WORKTREES[TRUNK])
    assert not any(argv[:2] in (["gh", "api"], ["gh", "stack"]) and argv[2] in ("rebase", "sync", "push") for argv, _ in calls)


def test_needs_rebase_layers_and_missing_pr_are_one_row_with_exact_details():
    stale = layer("layer-a", 1, "a" * 40, "9" * 40, needsRebase=True)
    nopr = {"name": "layer-b", "head": "b" * 40, "base": "a" * 40, "isMerged": False, "isQueued": False, "needsRebase": False}
    run, calls = stack_runner(stack_view(stale, nopr))

    _, rows = check(run)

    assert [row["id"] for row in rows["stack_incoherent"]] == [f"stack:{TRUNK}"]
    detail = rows["stack_incoherent"][0]["detail"]
    assert detail == (
        "layer-a (PR #1) needs rebase: its base 999999999 is not the head ttttttttt of integrate/phase-bj; "
        "the stack writer runs `gh stack rebase` in /fixture/code/a; "
        "layer-b has no PR; the stack writer runs `gh stack submit` in /fixture/code/b")
    assert sum(len(value) for value in rows.values()) == 1
    assert [argv for argv, _ in calls if argv[:2] == ["gh", "stack"]] == [["gh", "stack", "view", "--json"]]


def test_merged_layers_are_skipped_and_the_next_layer_is_checked_against_them():
    merged = layer("layer-a", 1, "a" * 40, "t" * 40, isMerged=True)
    run, _ = stack_runner(stack_view(merged, layer("layer-b", 2, "b" * 40, "a" * 40)))
    _, rows = check(run)
    assert rows["stack_incoherent"] == []


def test_local_fallback_with_a_coherent_chain_reports_only_the_degraded_row(capsys):
    run, calls = stack_runner(view_error=True, ancestors={(TRUNK, "layer-a"), ("layer-a", "layer-b")})

    note, rows = check(run)

    assert [row["id"] for row in rows["stack_incoherent"]] == [f"stack:{TRUNK}:gh-unavailable"]
    assert note.startswith(f"0 stack issue(s) on {TRUNK} (local git: ")
    assert [argv for argv, _ in calls if argv[:2] == ["git", "merge-base"]] == [
        ["git", "merge-base", "--is-ancestor", TRUNK, "layer-a"], ["git", "merge-base", "--is-ancestor", "layer-a", "layer-b"]]


def test_local_fallback_reports_a_broken_chain_read_only_without_calling_gh_for_it():
    run, calls = stack_runner(view_error=True, ancestors={(TRUNK, "layer-a")})

    _, rows = check(run)

    fault = next(row for row in rows["stack_incoherent"] if row["id"] == f"stack:{TRUNK}")
    assert fault["detail"] == (
        "layer-b does not contain the head of layer-a (`git merge-base --is-ancestor layer-a layer-b` exited 1); "
        "the stack writer rebases it onto layer-a in /fixture/code/b")
    assert not any(argv[0] == "git" and argv[1] in ("rebase", "merge", "reset", "push") for argv, _ in calls)
    assert not any(row for key, value in rows.items() if key != "stack_incoherent" for row in value)


def test_gh_failure_is_a_degraded_row_with_the_verbatim_error_and_never_a_stack_fault(capsys):
    run, _ = stack_runner(view_error=True, ancestors={(TRUNK, "layer-a"), ("layer-a", "layer-b")})

    note, rows = check(run)

    assert [row["id"] for row in rows["stack_incoherent"]] == [f"stack:{TRUNK}:gh-unavailable"]
    detail = rows["stack_incoherent"][0]["detail"]
    assert "fell back to local git" in detail and "needsRebase" in detail
    assert "gh stack view --json" in detail and "[1] current branch is not part of a stack" in detail
    assert detail in capsys.readouterr().err and "current branch is not part of a stack" in note


def test_unparseable_gh_json_falls_back_announcing_the_parse_error(capsys):
    run, _ = stack_runner(ancestors={(TRUNK, "layer-a"), ("layer-a", "layer-b")})

    def garbled(argv, fix, **kwargs):
        return Ok(Output(0, "{not json")) if argv == ["gh", "stack", "view", "--json"] else run(argv, fix, **kwargs)

    note, rows = check(garbled)

    detail = rows["stack_incoherent"][0]["detail"]
    assert rows["stack_incoherent"][0]["id"] == f"stack:{TRUNK}:gh-unavailable"
    assert "output is not JSON: Expecting property name enclosed in double quotes" in detail
    assert "(local git: " in note and detail in capsys.readouterr().err


def test_view_for_another_trunk_falls_back_saying_why(capsys):
    other = {"trunk": "main", "branches": []}
    run, _ = stack_runner(other, ancestors={(TRUNK, "layer-a"), ("layer-a", "layer-b")})

    _, rows = check(run)

    detail = rows["stack_incoherent"][0]["detail"]
    assert "is for trunk 'main' (expected 'integrate/phase-bj')" in detail
    assert detail in capsys.readouterr().err


def test_degraded_row_and_real_local_fault_are_both_reported():
    run, _ = stack_runner(view_error=True, ancestors={(TRUNK, "layer-a")})

    _, rows = check(run)

    assert [row["id"] for row in rows["stack_incoherent"]] == [f"stack:{TRUNK}:gh-unavailable", f"stack:{TRUNK}"]


def test_failed_merge_base_read_is_reported_not_treated_as_no_issue() -> None:
    failed = CommandFailed("command_failed", ("git", "merge-base", "--is-ancestor", TRUNK, "layer-a"), 128,
                           "fatal: bad object", "inspect it")
    run, _ = stack_runner(view_error=True)

    def failing(argv, fix, **kwargs):
        return Err(failed) if argv[:3] == ["git", "merge-base", "--is-ancestor"] else run(argv, fix, **kwargs)

    with pytest.raises(queues.QueueFailure) as raised:
        check(failing)
    assert "git merge-base --is-ancestor" in str(raised.value) and "fatal: bad object" in str(raised.value)
