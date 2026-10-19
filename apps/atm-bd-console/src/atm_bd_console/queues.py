"""Read-only phase queue report, backed by bounded bead and ATM reads."""
from __future__ import annotations

import json
import sys
import tomllib
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import atm_bd, command, config
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
    ("pr_unlinked", "PR on the phase, not on an open stack", "lead (link)"),
    ("stack_incoherent", "Open stack not coherent", "lead (stack writer)"),
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


def _resolve(args, repo_root: Path) -> tuple[str, str | None, Path]:
    if args.phase:
        result = atm_bd.phase_file(str(repo_root), args.phase)
        if isinstance(result, Err):
            raise QueueFailure(f"atm-bd queues --phase {args.phase}: {result.error.kind}: {result.error.fix}")
        code_repo = config.code_repository(repo_root, result.value.code_checkout)
        if isinstance(code_repo, Err):
            error = code_repo.error
            raise QueueFailure(f"atm-bd queues --phase {args.phase}: {error.kind}: {error.fix}")
        phase_path = repo_root / ".atm-bd" / f"phase-{args.phase}.toml"
        try:
            integration_branch = tomllib.loads(phase_path.read_text(encoding="utf-8")).get("integration_branch")
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise QueueFailure(f"{phase_path}: {error}: repair the phase file") from error
        return result.value.root_id, integration_branch, Path(code_repo.value[0])
    root = args.root
    phase = root.rsplit("-phase-", 1)[-1]
    if not phase or phase == root:
        raise QueueFailure(f"atm-bd queues --root {root}: root must be <prefix>-phase-<phase>: provide a phase root id")
    # The root row supplies the integration branch when --root bypasses the phase file.
    return root, None, repo_root


def _valid_id(row: dict[str, Any], command_name: str) -> str:
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise QueueFailure(f"{command_name}: every bead row must have a nonempty id: repair its JSON output")
    return value


def build(args, repo_root: Path, *, now: datetime | None = None, runner=command.run) -> dict[str, Any]:
    """Read open, ready, task and relevant closed rows once each and classify the phase."""
    now = now or datetime.now(UTC)
    root, integration_branch, code_root = _resolve(args, repo_root)
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
        closed_rows = _array(run_json(["bd", "list", "--status", "closed", "--id", ",".join(unknown), "--json"], cwd=repo_root, runner=runner), "bd list --status closed")
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
        stack_note = stacks(str(integration_branch), queues, now, code_root, runner) if integration_branch else "no integration_branch: stack not checked"
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


def _stack_view(trunk: str, candidates: list[str], repo_root: Path, runner=command.run) -> dict[str, Any]:
    paths = _worktrees(repo_root, runner)
    errors = []
    for branch in dict.fromkeys([trunk, *candidates]):
        worktree = paths.get(branch)
        if worktree is None:
            continue
        view = run_json(["gh", "stack", "view", "--json"], cwd=worktree, runner=runner)
        if isinstance(view, dict) and view.get("trunk") == trunk:
            return view
        errors.append(f"{branch}: stack view trunk did not match {trunk}")
        if len(errors) >= 5:
            break
    reason = "; ".join(errors) if errors else f"no worktree on {trunk} or an open stack layer"
    raise QueueFailure(f"gh stack view --json: {reason}: check out a worktree on the phase stack")


def stacks(trunk: str, queues: dict[str, list[dict[str, Any]]], now: datetime, repo_root: Path, runner=command.run) -> str:
    pages = run_json(["gh", "api", "--paginate", "--slurp", "repos/{owner}/{repo}/stacks?per_page=100"], cwd=repo_root, runner=runner)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise QueueFailure("gh api repos/{owner}/{repo}/stacks: expected pages of stack objects: repair GitHub JSON output")
    heads = [pr["head"]["ref"] for page in pages for stack in page if isinstance(stack, dict) and stack.get("open")
             and (stack.get("base") or {}).get("ref") in (trunk, None)
             for pr in reversed(stack.get("pull_requests") or [])
             if isinstance(pr, dict) and pr.get("state") == "open" and isinstance(pr.get("head"), dict)
             and pr["head"].get("ref")]
    view = _stack_view(trunk, heads, repo_root, runner)
    layers = [branch for branch in view.get("branches") or [] if not branch.get("isMerged") and branch.get("name") != trunk]
    numbers = {(branch.get("pr") or {}).get("number") for branch in layers} - {None}
    on_trunk = sorted((stack for page in pages for stack in page if isinstance(stack, dict) and stack.get("open") and (
        (stack.get("base") or {}).get("ref") == trunk or numbers & {pr.get("number") for pr in stack.get("pull_requests") or [] if isinstance(pr, dict)})),
                      key=lambda stack: stack.get("number", 0))

    def add(key: str, row_id: str, title: str, detail: str) -> None:
        queues[key].append({"queue": key, "id": row_id, "stage": "stack", "status": "open", "assignee": "-",
                            "priority": None, "since": None, "age": age(None, now), "title": title, "detail": detail})

    linked = {pr["number"] for stack in on_trunk for pr in stack.get("pull_requests") or [] if isinstance(pr, dict) and "number" in pr}
    for stack in on_trunk:
        mine = {pr["number"] for pr in stack.get("pull_requests") or [] if isinstance(pr, dict) and pr.get("state") == "open" and "number" in pr}
        problems = [f"{branch['name']} needs rebase" for branch in layers if branch.get("needsRebase")]
        problems += [f"{branch['name']} has no PR" for branch in layers if not branch.get("pr")]
        problems += [f"#{number} not in the local stack" for number in sorted(mine - numbers)]
        if len(on_trunk) > 1:
            problems.append(f"{len(on_trunk)} open stacks on {trunk}: not landable in one merge")
        if problems:
            add("stack_incoherent", f"stack#{stack['number']}", f"stack #{stack['number']} on {trunk}", "; ".join(problems))
    loose = [branch for branch in layers if branch.get("pr") and branch["pr"].get("number") not in linked]
    if not on_trunk and len(layers) == 1:
        loose = []
    for branch in loose:
        add("pr_unlinked", f"pr#{branch['pr']['number']}", branch["name"], f"{branch['name']} on the {trunk} stack")
    return f"{len(on_trunk)} open stack(s) on {trunk}, {len(layers)} unmerged layer(s) in the local stack"


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
