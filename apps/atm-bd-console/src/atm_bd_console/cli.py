"""Command line launch modes for the ATM BD console."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime

from . import atm_bd, render, runner
from . import config as config_source
from .build import identity
from .codec import snapshot_stale, to_json
from .paths import CONSOLE_NAME
from .result import Err, Error, IoFailed, Ok
from .state import acquire_lock, load_snapshot
from .types import INITIAL_STATE


def now() -> datetime:
    return datetime.now(UTC)


def _error_text(error: Error) -> str:
    if isinstance(error, IoFailed):
        return f"{error.kind}: {error.path}: {error.message}: {error.fix}"
    return f"{error.kind}: {error.fix}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=CONSOLE_NAME)
    parser.add_argument("command", choices=("show", "snapshot"), nargs="?")
    parser.add_argument("--version", action="version", version=identity())
    parser.add_argument("--phase")
    parser.add_argument("--github", choices=("graphql", "rest", "off")); parser.add_argument("--interval", type=int)
    parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--headless", action="store_true", help="run one tick and exit without starting the app (cron mode)")
    args = parser.parse_args(argv)
    if not args.phase:
        print(f"{CONSOLE_NAME}: --phase <phase> is required; name the phase to run")
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
        print(f"{CONSOLE_NAME} is not running for phase {args.phase}{last}; start it: {CONSOLE_NAME} --phase {args.phase}")
        return 2
    loaded = config_source.load_config(repo_root, args.phase, github=args.github, interval_s=args.interval, dry_run=args.dry_run or args.command == "snapshot", no_fetch=args.no_fetch)
    if isinstance(loaded, Err): print(f"{CONSOLE_NAME}: {loaded.error.kind}: {loaded.error.fix}"); return 3
    check = atm_bd.repo_config(loaded.value.repo_root)
    if isinstance(check, Err): print(f"{CONSOLE_NAME}: {check.error.kind}: {check.error.fix}"); return 3
    for command in ("bd", "atm", "git"):
        if shutil.which(command) is None:
            print(f"{CONSOLE_NAME}: not_found: install {command} and retry")
            return 3
    if args.headless or args.command == "snapshot":
        lock = acquire_lock(loaded.value.repo_root, root_id)
        if isinstance(lock, Err):
            print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": _error_text(lock.error)}))
            snapshot = load_snapshot(loaded.value.repo_root, root_id)
            if isinstance(snapshot, Ok) and snapshot_stale(snapshot.value, now()):
                text = f"{CONSOLE_NAME} stopped updating (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})"
                print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": text}))
            return 0
        with lock.value:
            saved = lock.value.load_state()
            state = saved.value if isinstance(saved, Ok) else INITIAL_STATE
            snapshot, _ = runner.step(loaded.value, state, False, lock.value)
        if args.headless and snapshot.stopped is None:
            for action in (item for item in snapshot.sent if item.at == snapshot.generated_at):
                print(to_json({"at": action.at, "kind": action.kind, "to": action.to, "bead": action.bead_ids[0] if action.bead_ids else None, "text": action.text, "outcome": action.outcome or "sent"}))
        if args.headless:
            for error in snapshot.errors:
                print(json.dumps({"at": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": _error_text(error)}))
        return 0
    from .app import run

    return run(loaded.value, None, INITIAL_STATE, None, runner_step=runner.step)
