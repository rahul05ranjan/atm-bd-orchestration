"""Read-only phase queue report, backed by bounded bead and ATM reads."""
from __future__ import annotations

import json
import sys
import tomllib
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import atm_bd, command, config
from .log import error_message
from .result import CommandFailed, Err, Timeout

SCHEMA = "atm-bd-orchestration/bead-queues/v1"
FAILED = 2
SANITY, QA, FINDING, PLAN_REVIEW = "stage:dev-sanity", "stage:qa", "stage:finding", "stage:plan-review"
WORK = {"stage:dev", "stage:fix", FINDING}
QUEUES = [
    ("in_flight", "In flight (ATM task open)", "assignee"),
    ("task_bead_closed", "ATM task open, bead closed", "assignee (close task)"),
    ("task_stalled", "Active task, assignee stopped (reminders unanswered)", "lead -> re-engage assignee"),
    ("claimed_no_task", "Claimed (in_progress), no ATM task", "lead (verify)"),
    ("needs_plan_review", "Plan review ready -> needs dispatch", "lead -> quality-mgr"),
    ("needs_dev", "Dev ready -> needs dispatch", "lead -> dev"),
    ("needs_sanity", "Dev complete -> needs sanity dispatch", "lead -> dev-sanity"),
    ("needs_dev_fix", "Sanity FAIL -> needs dev-fix dispatch", "lead -> dev"),
    ("needs_qa", "Sanity PASS -> needs QA dispatch", "lead -> quality-mgr"),
    ("needs_fix", "QA FAIL -> fix ready, needs dispatch", "lead -> dev"),
    ("needs_finding_fix", "Finding -> needs fix dispatch", "lead -> dev"),
    ("minor_backlog", "Minor finding (backlog)", "lead (schedule)"),
    ("container_closable", "All children closed -> close it", "lead"),
    ("ready_other", "Other ready, no task", "lead"),
    ("stack_incoherent", "Stack needs the stack writer", "lead (stack writer)"),
    ("not_ready", "Not ready (prerequisite open)", "-"),
]
QUIET = {"in_flight", "not_ready"}
STALL_REMINDERS = 3
NOT_READY_KINDS = {
    "sprint": "sprint", "group": "sprint", "dev": "dev", "fix": "fix", "finding": "fix",
    "dev-sanity": "sane", "sanity": "sane", "qa": "qa", "plan-review": "review", "review": "review",
}


class QueueFailure(Exception):
    """A failed or malformed source read; no partial report should be shown."""


def _command_error(result: Err) -> str:
    error = result.error
    if isinstance(error, CommandFailed):
        detail = (error.stderr.splitlines()[-1] if error.stderr.splitlines() else f"exit {error.code}")
        return f"{' '.join(error.argv)}: {detail}: {error.fix}"
    if isinstance(error, Timeout):
        return f"{' '.join(error.argv)}: timed out after {error.seconds}s: {error.fix}"
    return f"{error.kind}: {error}: inspect the command output and repair the source"


def run_json(argv: list[str], *, cwd: str | Path, runner=command.run) -> Any:
    """Run one read command with a 20-second bound and decode its JSON response."""
    result = runner(argv, f"repair the source and retry `{' '.join(argv)}`", cwd=cwd)
    if isinstance(result, Err):
        raise QueueFailure(_command_error(result))
    try:
        return json.loads(result.value.stdout or "[]")
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise QueueFailure(f"{' '.join(argv)}: invalid JSON ({error}): repair the command's JSON output") from error


def _array(value: Any, command_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise QueueFailure(f"{command_name}: expected a JSON array of objects: repair its JSON output")
    return value


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def age(value: str | None, now: datetime) -> str:
    stamp = _timestamp(value)
    if stamp is None:
        return "-"
    minutes = int((now - stamp).total_seconds() // 60)
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}" if minutes < 2880 else f"{minutes // 1440}d"


def _stage(bead: dict[str, Any]) -> str:
    return next((label for label in bead.get("labels") or [] if isinstance(label, str) and label.startswith("stage:")), "-")


def _meta(bead: dict[str, Any]) -> dict[str, Any]:
    metadata = bead.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _not_ready_kind(bead: dict[str, Any]) -> str:
    metadata = _meta(bead)
    if metadata.get("sanity_finding"):
        return "fix"
    for value in (_stage(bead).removeprefix("stage:"), metadata.get("template_kind"), metadata.get("kind"), metadata.get("role")):
        if isinstance(value, str) and value in NOT_READY_KINDS:
            return NOT_READY_KINDS[value]
    return "other"


def _resolve(args, repo_root: Path) -> tuple[str, str | None, Path, str | None]:
    if args.phase:
        result = atm_bd.phase_file(str(repo_root), args.phase)
        if isinstance(result, Err):
            raise QueueFailure(f"atm-bd queues --phase {args.phase}: {error_message(asdict(result.error))}")
        code_repo = config.code_repository(repo_root, result.value.code_checkout)
        if isinstance(code_repo, Err):
            raise QueueFailure(f"atm-bd queues --phase {args.phase}: {error_message(asdict(code_repo.error))}")
        phase_path = repo_root / ".atm-bd" / f"phase-{args.phase}.toml"
        try:
            integration_branch = tomllib.loads(phase_path.read_text(encoding="utf-8")).get("integration_branch")
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise QueueFailure(f"{phase_path}: {error}: repair the phase file") from error
        return result.value.root_id, integration_branch, Path(code_repo.value[0]), code_repo.value[1]
    root = args.root
    phase = root.rsplit("-phase-", 1)[-1]
    if not phase or phase == root:
        raise QueueFailure(f"atm-bd queues --root {root}: root must be <prefix>-phase-<phase>: provide a phase root id")
    # The root row supplies the integration branch when --root bypasses the phase file.
    return root, None, repo_root, None


def _valid_id(row: dict[str, Any], command_name: str) -> str:
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise QueueFailure(f"{command_name}: every bead row must have a nonempty id: repair its JSON output")
    return value


def build(args, repo_root: Path, *, now: datetime | None = None, runner=command.run) -> dict[str, Any]:
    """Read open, ready, task and relevant closed rows once each and classify the phase."""
    now = now or datetime.now(UTC)
    root, integration_branch, code_root, _ = _resolve(args, repo_root)
    phase = root.rsplit("-phase-", 1)[-1]
    label = f"phase-{phase}"
    scoped = ["-l", label, "--exclude-label", "anchor", "-n", "0", "--json"]
    beads = _array(run_json(["bd", "--readonly", "list", *scoped], cwd=repo_root, runner=runner), "bd list")
    by_id = {_valid_id(bead, "bd list"): bead for bead in beads}
    if root not in by_id:
        raise QueueFailure(f"bd list: root bead {root} is closed, missing, or lacks {label}: restore the open phase root")
    integration_branch = integration_branch or _meta(by_id[root]).get("integration_branch")
    ready_rows = _array(run_json(["bd", "ready", *scoped], cwd=repo_root, runner=runner), "bd ready")
    ready = {_valid_id(bead, "bd ready") for bead in ready_rows}
    tasks = _array(run_json(["atm", "task", "list", "--all", "--json"], cwd=repo_root, runner=runner), "atm task list")
    task_by_id = {}
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise QueueFailure("atm task list --all --json: every task row must have task_id: repair its JSON output")
        task_by_id[task_id] = task
    unknown = sorted(set(task_by_id) - set(by_id))
    closed: dict[str, dict[str, Any]] = {}
    if unknown:
        closed_rows = _array(run_json(["bd", "list", "--status", "closed", "-n", "0", "--id", ",".join(unknown), "--json"], cwd=repo_root, runner=runner), "bd list --status closed")
        closed = {_valid_id(bead, "bd list --status closed"): bead for bead in closed_rows}

    children: dict[str, list[dict[str, Any]]] = {}
    for bead in beads:
        parent = bead.get("parent")
        if isinstance(parent, str) and parent:
            children.setdefault(parent, []).append(bead)
    scope = set(by_id) - {root}

    def deps(bead: dict[str, Any], kind: str | None = None) -> list[str]:
        return [dep["depends_on_id"] for dep in bead.get("dependencies") or []
                if isinstance(dep, dict) and dep.get("issue_id", bead["id"]) == bead["id"]
                and (kind is None or dep.get("type") == kind) and isinstance(dep.get("depends_on_id"), str)]

    def sanity_child(bead: dict[str, Any]) -> bool:
        parent = by_id.get(bead.get("parent") or "")
        return bool(_meta(bead).get("sanity_finding")) or (
            parent is not None and _stage(parent) in WORK and not deps(bead, "discovered-from"))

    queues: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in QUEUES}

    def add(key: str, bead: dict[str, Any], when: str | None, detail: str = "") -> None:
        bead_id = _valid_id(bead, "queue row")
        row = {"queue": key, "id": bead_id, "stage": _stage(bead).removeprefix("stage:"),
               "status": bead.get("status", "unknown"), "assignee": bead.get("assignee") or "-",
               "priority": bead.get("priority"), "since": when, "age": age(when, now),
               "title": bead.get("title", ""), "detail": detail}
        if key == "not_ready":
            row["kind"] = _not_ready_kind(bead)
        queues[key].append(row)

    for task_id, task in task_by_id.items():
        bead = by_id.get(task_id) if task_id in scope else closed.get(task_id)
        if bead is None or _stage(bead) == "stage:stack":
            continue
        key = "task_bead_closed" if bead.get("status") == "closed" else "in_flight"
        if key == "in_flight" and task.get("state") == "active" and (task.get("reminder_count") or 0) >= STALL_REMINDERS:
            key = "task_stalled"
        when = bead.get("closed_at") if key == "task_bead_closed" else task.get("assigned_at")
        add(key, bead, when, f"task {task.get('state')}, assignee {task.get('assignee')}, {task.get('reminder_count', 0)} reminders")

    for bead_id in sorted(scope):
        bead = by_id[bead_id]
        stage, metadata = _stage(bead), _meta(bead)
        if stage == "stage:stack" or bead_id in task_by_id:
            continue
        open_children = children.get(bead_id, [])
        since = bead.get("updated_at") or bead.get("created_at")
        if stage == "stage:sprint" and not open_children:
            add("container_closable", bead, since, "no open children")
            continue
        if bead.get("status") == "in_progress":
            add("claimed_no_task", bead, bead.get("started_at") or bead.get("updated_at"))
            continue
        if open_children and stage in WORK and any(sanity_child(child) for child in open_children):
            count = sum(sanity_child(child) for child in open_children)
            add("needs_dev_fix", bead, since, f"{count} open sanity finding(s)")
            continue
        if sanity_child(bead) and bead.get("parent") in by_id:
            add("not_ready", bead, since, f"handled by dev-fix for {bead['parent']}")
            continue
        if bead.get("status") != "open" or bead_id not in ready:
            blockers = [dep for dep in deps(bead, "blocks") if dep in by_id]
            add("not_ready", bead, since, "blocked by " + ",".join(blockers) if blockers else f"status {bead.get('status')}")
            continue
        if open_children:
            add("not_ready", bead, since, "open children " + ",".join(child["id"] for child in open_children[:3]))
        elif stage == PLAN_REVIEW:
            add("needs_plan_review", bead, since)
        elif stage == "stage:dev":
            add("needs_dev", bead, since, f"difficulty {metadata.get('difficulty', '-')}")
        elif stage == "stage:fix":
            add("needs_fix", bead, since, f"{metadata.get('finding_ref', '-')} round {metadata.get('round', '-')} filed by {metadata.get('filed_by', '-')}")
        elif stage == SANITY:
            checked = metadata.get("dev_bead") or metadata.get("checked_bead")
            if not checked:
                add("not_ready", bead, since, "no paired bead (metadata dev_bead)")
            elif checked in by_id:
                add("not_ready", bead, since, f"checked {checked} {by_id[checked].get('status')}")
            else:
                add("needs_sanity", bead, since, f"checks {checked}")
        elif stage == QA:
            add("needs_qa", bead, since, "quick fix (no sanity)" if metadata.get("quick_fix") else "sanity passed")
        elif stage == FINDING:
            key = "minor_backlog" if str(metadata.get("severity", "")).lower() == "minor" else "needs_finding_fix"
            add(key, bead, since, f"{metadata.get('severity', '-')} by {metadata.get('reviewer', '-')}")
        else:
            add("ready_other", bead, since)

    stack_note = "not checked (--stack reads it)"
    if args.stack:
        if not integration_branch:
            stack_note = "no integration_branch: stack not checked"
        else:
            metas = [_meta(bead) for bead in beads]
            layered = sorted((meta["layer"], meta["branch"]) for meta in metas
                             if isinstance(meta.get("layer"), int) and not isinstance(meta["layer"], bool)
                             and isinstance(meta.get("branch"), str) and meta["branch"])
            layers = tuple(dict.fromkeys(branch for _, branch in layered))
            stack_note = stacks(str(integration_branch), queues, now, code_root, runner, layers)
    for rows in queues.values():
        rows.sort(key=lambda row: (row["priority"] if row["priority"] is not None else 9, row["id"]))
    return {"schema": SCHEMA, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "root": root,
            "integration_branch": integration_branch, "stack": stack_note, "queues": queues}


def _worktrees(repo_root: Path, runner=command.run) -> dict[str, str]:
    result = runner(["git", "worktree", "list", "--porcelain"], "inspect the phase code checkout", cwd=repo_root)
    if isinstance(result, Err):
        raise QueueFailure(_command_error(result))
    found: dict[str, str] = {}
    path: str | None = None
    for line in result.value.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[9:]
        elif line.startswith("branch refs/heads/") and path:
            found[line[18:]] = path
    return found


def _stack_view(trunk: str, candidates: list[str], paths: dict[str, str], runner=command.run) -> tuple[dict[str, Any] | None, list[str]]:
    """The one `gh stack view --json` of the phase stack, read from the trunk worktree or, failing that, a layer worktree.
    Returns (view, reasons). The view is None when no worktree can produce it (gh missing, no API, not a stack); the reasons then
    carry every attempt's verbatim error so the caller can announce the fallback to local git."""
    reasons: list[str] = []
    for branch in list(dict.fromkeys([trunk, *candidates]))[:5]:
        worktree = paths.get(branch)
        if worktree is None:
            continue
        result = runner(["gh", "stack", "view", "--json"], "run `gh stack view --json` in a worktree of the phase stack", cwd=worktree)
        if isinstance(result, Err):
            reasons.append(f"{branch} ({worktree}): {error_message(asdict(result.error))}")
            continue
        try:
            view = json.loads(result.value.stdout)
        except ValueError as error:
            reasons.append(f"{branch} ({worktree}): `gh stack view --json` output is not JSON: {error}")
            continue
        if isinstance(view, dict) and view.get("trunk") == trunk and isinstance(view.get("branches"), list):
            return view, reasons
        got = view.get("trunk") if isinstance(view, dict) else type(view).__name__
        reasons.append(f"{branch} ({worktree}): `gh stack view --json` is for trunk {got!r} (expected {trunk!r}) "
                       f"or has no `branches` list")
    if not reasons:
        reasons.append(f"no worktree of {trunk} or its layers to run `gh stack view --json` in")
    return None, reasons


def _sha(value: Any) -> str:
    return str(value)[:9] if value else "unknown"


def _view_issues(view: dict[str, Any], trunk: str, paths: dict[str, str]) -> list[str]:
    """Every stack fault in one view: a layer needing rebase, or an unmerged layer with no PR. Reports only; never changes the stack."""
    issues, lower, lower_head = [], trunk, None
    for branch in view["branches"]:
        name = branch.get("name")
        if name == trunk:
            lower, lower_head = trunk, branch.get("head")
            continue
        if not branch.get("isMerged"):
            where = paths.get(name) or paths.get(trunk) or "<stack worktree>"
            pr = branch.get("pr") or {}
            label = f"{name} (PR #{pr['number']})" if pr.get("number") else name
            if branch.get("needsRebase"):
                issues.append(f"{label} needs rebase: its base {_sha(branch.get('base'))} is not the head {_sha(lower_head)} of {lower}; "
                              f"the stack writer runs `gh stack rebase` in {where}")
            if not pr:
                issues.append(f"{name} has no PR; the stack writer runs `gh stack submit` in {where}")
        lower, lower_head = name, branch.get("head")
    return issues


def _local_issues(trunk: str, layers: list[str], paths: dict[str, str], repo_root: Path, runner=command.run) -> list[str]:
    """Read-only fallback when gh cannot give the stack: each layer must contain the head of the layer below."""
    issues, lower = [], trunk
    for name in layers:
        if name not in paths or lower not in (paths.keys() | {trunk}):
            continue
        fix = f"inspect `git merge-base --is-ancestor {lower} {name}`"
        result = runner(["git", "merge-base", "--is-ancestor", lower, name], fix, cwd=repo_root, ok_codes=(0, 1))
        if isinstance(result, Err):
            raise QueueFailure(_command_error(result))
        if result.value.code == 1:
            issues.append(f"{name} does not contain the head of {lower} (`git merge-base --is-ancestor {lower} {name}` exited 1); "
                          f"the stack writer rebases it onto {lower} in {paths[name]}")
        lower = name
    return issues


def _stack_row(row_id: str, title: str, detail: str, now: datetime) -> dict[str, Any]:
    return {"queue": "stack_incoherent", "id": row_id, "stage": "stack", "status": "open", "assignee": "-",
            "priority": None, "since": None, "age": age(None, now), "title": title, "detail": detail}


def stacks(trunk: str, queues: dict[str, list[dict[str, Any]]], now: datetime, repo_root: Path, runner=command.run,
           layers: tuple[str, ...] = ()) -> str:
    """One stack check: one `gh stack view --json`, or the local read-only fallback, producing at most one queue row."""
    paths = _worktrees(repo_root, runner)
    view, reasons = _stack_view(trunk, list(reversed(layers)), paths, runner)
    if view is not None:
        issues, source = _view_issues(view, trunk, paths), "gh stack view"
    else:
        issues, source = _local_issues(trunk, list(layers), paths, repo_root, runner), "local git"
        degraded = ("gh stack view unavailable, fell back to local git, which cannot see needsRebase or missing-PR faults: "
                    + "; ".join(reasons))
        print(f"atm-bd queues: {degraded}", file=sys.stderr)
        source += ": " + "; ".join(reasons)
        queues["stack_incoherent"].append(_stack_row(f"stack:{trunk}:gh-unavailable", f"stack on {trunk} (degraded)", degraded, now))
    if issues:
        queues["stack_incoherent"].append(_stack_row(f"stack:{trunk}", f"stack on {trunk}", "; ".join(issues), now))
    return f"{len(issues)} stack issue(s) on {trunk} ({source})"


def cron(report: dict[str, Any], state_file: Path, min_age: int, *, now: datetime | None = None) -> dict[str, Any] | None:
    try:
        seen = set(json.loads(state_file.read_text(encoding="utf-8")).get("reported", []))
    except FileNotFoundError:
        seen = set()
    except (OSError, ValueError, AttributeError) as error:
        raise QueueFailure(f"{state_file}: {error}: repair or remove the queue state file") from error
    now = now or datetime.now(UTC)
    present, new_rows = set(), []
    for key, rows in report["queues"].items():
        if key in QUIET:
            continue
        for row in rows:
            identity = f"{key}:{row['id']}"
            entered = _timestamp(row["since"])
            if min_age > 0 and entered is not None and now - entered < timedelta(minutes=min_age):
                continue
            present.add(identity)
            if identity not in seen:
                new_rows.append(row)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if state_file.parent.name == "bead-queues" and not (state_file.parent / ".gitignore").exists():
        (state_file.parent / ".gitignore").write_text("*\n", encoding="utf-8")
    state_file.write_text(json.dumps({"schema": SCHEMA, "reported": sorted(present)}, indent=1) + "\n", encoding="utf-8")
    if not new_rows:
        return None
    labels = {key: (label, actor) for key, label, actor in QUEUES}
    for row in new_rows:
        row["label"], row["next_actor"] = labels[row["queue"]]
    return {key: report[key] for key in ("schema", "generated_at", "root", "integration_branch", "stack")} | {"rows": new_rows}


def _short(text: str, size: int) -> str:
    return text if len(text) <= size else text[:size - 1] + "…"


def render(report: dict[str, Any], show_all: bool) -> str:
    queues = report["queues"]
    lines = [f"Bead queues · descendants of {report['root']} · {report['generated_at']}", f"stack: {report['stack']}", "",
             f"  {'queue':42} {'count':>5}  next actor"]
    for key, label, actor in QUEUES:
        lines.append(f"  {label:42} {len(queues[key]):>5}  {actor}")
    for key, label, _ in QUEUES:
        if not queues[key] or (key == "not_ready" and not show_all):
            continue
        lines.extend((f"\n== {label} ({len(queues[key])})",))
        for row in queues[key]:
            lines.append(f"  {_short(row['id'], 44):44} {_short(str(row['assignee']), 10):10} {row['age']:>6}  {_short(str(row['detail'] or row['title']), 70)}")
    if queues["not_ready"] and not show_all:
        counts = Counter(row["kind"] for row in queues["not_ready"])
        summary = " | ".join(f"{counts[kind]} {kind}" for kind in ("sprint", "dev", "fix", "sane", "qa", "review", "other") if counts[kind])
        lines.append(f"\nbd not-ready: {summary}")
    return "\n".join(lines)


def main(args, *, repo_root: Path, runner=command.run, now: datetime | None = None) -> int:
    try:
        report = build(args, repo_root, now=now, runner=runner)
        if not args.json:
            print(render(report, args.all))
            return 0
        state_file = Path(args.state_file) if args.state_file else repo_root / ".atm-bd" / "bead-queues" / f"{report['root']}.json"
        result = cron(report, state_file, args.min_age, now=now)
        if result:
            print(json.dumps(result, indent=1))
        return 0
    except (QueueFailure, OSError, RuntimeError) as error:
        print(f"atm-bd queues: {error}", file=sys.stderr)
        return FAILED
