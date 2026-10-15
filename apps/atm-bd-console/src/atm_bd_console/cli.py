"""Command line launch modes for the ATM BD console."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, atm_bd, render, runner
from . import config as config_source
from .codec import snapshot_stale, to_json
from .paths import CONSOLE_NAME
from .result import Err, Ok
from .state import acquire_lock, load_snapshot, load_state
from .types import State

MISSING_AS = f"""{CONSOLE_NAME}: --as <phase lead> is required: the ATM identity that leads this phase.
Tasks are assigned as that identity, and escalations go to it when no escalation recipient is set.
The phase lead varies by phase, so it is never inferred from $ATM_IDENTITY or the repository's lead.
Example: {CONSOLE_NAME} --phase <x> --as fenix"""
EMPTY = State((), None, None, None, None, (), (), {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=CONSOLE_NAME)
    parser.add_argument("command", choices=("show", "snapshot"), nargs="?")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--phase"); parser.add_argument("--as", dest="lead")
    parser.add_argument("--github", choices=("graphql", "rest", "off")); parser.add_argument("--interval", type=int)
    parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    if not args.phase:
        print(f"{CONSOLE_NAME}: --phase <phase> is required; name the phase to run")
        return 3
    root_id = f"atm-phase-{args.phase}"
    if args.command == "show":
        snapshot = load_snapshot(Path.cwd(), root_id)
        if isinstance(snapshot, Ok) and (snapshot.value.stopped is not None or not snapshot_stale(snapshot.value, datetime.now(UTC))):
            print(render.dashboard(snapshot.value, 120, 80).plain)
            if snapshot.value.stopped is None: print(render.status_bar(snapshot.value, 120).plain)
            return 0
        last = f" (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})" if isinstance(snapshot, Ok) else ""
        lead = snapshot.value.lead if isinstance(snapshot, Ok) else "<lead>"
        print(f"{CONSOLE_NAME} is not running for phase {args.phase}{last}; start it: {CONSOLE_NAME} --phase {args.phase} --as {lead}")
        return 2
    if not args.lead:
        print(MISSING_AS); return 3
    loaded = config_source.load_config(Path.cwd(), args.phase, args.lead, github=args.github, interval_s=args.interval, dry_run=args.dry_run or args.command == "snapshot", no_fetch=args.no_fetch)
    if isinstance(loaded, Err): print(f"{CONSOLE_NAME}: {loaded.error.kind}: {loaded.error.fix}"); return 3
    for check in (atm_bd.phase_file(loaded.value.repo_root, args.phase), atm_bd.repo_config(loaded.value.repo_root)):
        if isinstance(check, Err): print(f"{CONSOLE_NAME}: {check.error.kind}: {check.error.fix}"); return 3
    for command in ("bd", "atm", "git"):
        if shutil.which(command) is None:
            print(f"{CONSOLE_NAME}: not_found: install {command} and retry")
            return 3
    saved = load_state(loaded.value.repo_root, root_id)
    state = saved.value if isinstance(saved, Ok) else EMPTY
    if args.headless or args.command == "snapshot":
        lock = acquire_lock(loaded.value.repo_root, root_id)
        if isinstance(lock, Err):
            snapshot = load_snapshot(loaded.value.repo_root, root_id)
            if isinstance(snapshot, Ok) and snapshot_stale(snapshot.value, datetime.now(UTC)):
                text = f"{CONSOLE_NAME} stopped updating (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})"
                print(json.dumps({"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": text}))
            return 0
        with lock.value:
            snapshot, _ = runner.step(loaded.value, state, False, lock.value)
        if args.headless and snapshot.stopped is None:
            for action in (item for item in snapshot.sent if item.at == snapshot.generated_at):
                print(to_json({"at": action.at, "kind": action.kind, "to": action.to, "bead": action.bead_ids[0] if action.bead_ids else None, "text": action.text}))
            for error in snapshot.errors:
                print(json.dumps({"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "error", "text": f"{error.kind}: {error.fix}"}))
        return 0
    from .app import run

    return run(loaded.value, runner.step)
