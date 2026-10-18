"""Command line launch modes for the ATM BD console."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from datetime import UTC, datetime

from . import atm_bd, atm_source, render, runner, status_table
from . import config as config_source
from .build import identity
from .codec import snapshot_stale, to_json
from .paths import CONSOLE_NAME
from .result import CommandFailed, Err, Error, IoFailed, NotFound, Ok
from .state import acquire_lock, load_snapshot
from .types import INITIAL_STATE, Config


def now() -> datetime:
    return datetime.now(UTC)


def _error_text(error: Error) -> str:
    if isinstance(error, IoFailed):
        return f"{error.kind}: {error.path}: {error.message}: {error.fix}"
    return f"{error.kind}: {error.fix}"


def _notification_startup_error(config: Config) -> Error | None:
    sender, team = config.notification_sender.split("@", 1)
    fix = f"run atm teams add-member {team} {sender} --agent-type tool --model none to provision {config.notification_sender}"
    members = atm_source.members(team, sender)
    if isinstance(members, Err):
        if isinstance(members.error, CommandFailed) and f"team '{team}' was not found" in members.error.stderr:
            return NotFound("not_found", config.notification_sender, fix)
        return replace(members.error, fix=f"could not verify {config.notification_sender}; {fix}; {members.error.fix}")
    if not any(member.name == sender for member in members.value):
        return NotFound("not_found", config.notification_sender, fix)
    return None


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    # Skill has its own parser; dispatch it before the console parser to pass through its options.
    if values and values[0] == "skill":
        from .skill import run as run_skill

        return run_skill(values[1:])

    parser = argparse.ArgumentParser(prog=CONSOLE_NAME)
    parser.add_argument("--version", action="version", version=identity())
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("console", "cron", "show", "snapshot"):
        sub = commands.add_parser(name, help="run one tick and exit" if name == "cron" else None)
        sub.add_argument("--phase")
        if name == "cron":
            sub.add_argument("--md-table", action="store_true")
        if name != "show":
            sub.add_argument("--github", choices=("graphql", "rest", "off"))
            sub.add_argument("--interval", type=int)
            sub.add_argument("--github-poll-interval", type=int, help="GitHub polling interval in seconds (default 600)")
            sub.add_argument("--github-quota-threshold", type=int, help="minimum remaining quota percentage (default 50)")
            if name != "snapshot":
                sub.add_argument("--dry-run", action="store_true")
            sub.add_argument("--no-fetch", action="store_true")
    # Help-only: `skill` is dispatched above, before this parser reads its options, so this entry never parses.
    commands.add_parser("skill", help="install or list atm-bd-orchestration skills; `skill --help` lists its options")
    args = parser.parse_args(argv)
    invocation = f"{CONSOLE_NAME} {args.command}"
    if not args.phase:
        print(f"{invocation}: --phase <phase> is required; name the phase to run")
        return 3
    repo_root = config_source.repo_root()
    root_id = f"atm-phase-{args.phase}"
    if args.command == "show":
        snapshot = load_snapshot(repo_root, root_id)
        if isinstance(snapshot, Ok) and (snapshot.value.stopped is not None or not snapshot_stale(snapshot.value, now())):
            print(render.dashboard(snapshot.value, 120, 80).plain)
            if snapshot.value.stopped is None: print(render.status_bar(snapshot.value, 120).plain)
            return 0
        last = f" (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})" if isinstance(snapshot, Ok) else ""
        print(f"{invocation}: console is not running for phase {args.phase}{last}; start it: {CONSOLE_NAME} console --phase {args.phase}")
        return 2
    md_table = getattr(args, "md_table", False)
    loaded = config_source.load_config(repo_root, args.phase, github=args.github, interval_s=args.interval, github_poll_interval_s=args.github_poll_interval, github_quota_threshold=args.github_quota_threshold, dry_run=getattr(args, "dry_run", False) or args.command == "snapshot" or md_table, no_fetch=args.no_fetch)
    if isinstance(loaded, Err): print(f"{invocation}: {loaded.error.kind}: {loaded.error.fix}"); return 3
    check = atm_bd.repo_config(loaded.value.repo_root)
    if isinstance(check, Err): print(f"{invocation}: {check.error.kind}: {check.error.fix}"); return 3
    for command in ("bd", "atm", "git"):
        if shutil.which(command) is None:
            print(f"{invocation}: not_found: install {command} and retry")
            return 3
    if args.command in ("cron", "snapshot"):
        lock = acquire_lock(loaded.value.repo_root, root_id)
        if isinstance(lock, Err):
            if md_table:
                print(f"{invocation}: {_error_text(lock.error)}", file=sys.stderr)
                return 2
            print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": f"{invocation}: {_error_text(lock.error)}"}))
            snapshot = load_snapshot(loaded.value.repo_root, root_id)
            if isinstance(snapshot, Ok) and snapshot_stale(snapshot.value, now()):
                text = f"{invocation}: console stopped updating (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})"
                print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": text}))
            return 0
        with lock.value:
            saved = lock.value.load_state()
            state = saved.value if isinstance(saved, Ok) else INITIAL_STATE
            result = runner.step(loaded.value, state, md_table, lock.value)
            snapshot, _ = result
        if md_table:
            if snapshot.errors or snapshot.phase_data is None or snapshot.gh.truncated:
                detail = (_error_text(snapshot.errors[0]) if snapshot.errors else
                          "partial_snapshot: phase unavailable" if snapshot.phase_data is None else
                          "partial_snapshot: GitHub data truncated")
                print(f"{invocation}: {detail}", file=sys.stderr)
                return 2
            print(status_table.markdown(status_table.rows(snapshot)))
            return 0
        if args.command == "cron" and snapshot.stopped is None:
            for action in result.reported:
                print(to_json({"at": action.at, "kind": action.kind, "to": action.to, "bead": action.bead_ids[0] if action.bead_ids else None, "text": action.text, "outcome": action.outcome}))
        if args.command == "cron":
            for error in snapshot.errors:
                print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": f"{invocation}: {_error_text(error)}"}))
        return 0
    from .app import run

    return run(loaded.value, None, INITIAL_STATE, _notification_startup_error(loaded.value), runner_step=runner.step)
