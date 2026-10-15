"""Command line launch modes for the ATM BD console."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, app, render, runner
from . import config as config_source
from .codec import snapshot_stale
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
    parser.add_argument("--phase", required=True); parser.add_argument("--as", dest="lead")
    parser.add_argument("--github", choices=("graphql", "rest", "off")); parser.add_argument("--interval", type=int)
    parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--once", action="store_true"); parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    root_id = f"atm-phase-{args.phase}"
    if args.command == "show":
        snapshot = load_snapshot(Path.cwd(), root_id)
        if isinstance(snapshot, Ok) and not snapshot_stale(snapshot.value, datetime.now(UTC)):
            print(render.dashboard(snapshot.value, 120, 80).plain)
            if snapshot.value.stopped is None: print(render.status_bar(snapshot.value, 120).plain)
            return 0
        last = f" (last tick {snapshot.value.generated_at.astimezone().strftime('%H:%M')})" if isinstance(snapshot, Ok) else ""
        print(f"{CONSOLE_NAME} is not running for phase {args.phase}{last}; start it: {CONSOLE_NAME} --phase {args.phase} --as <lead>")
        return 2
    if not args.lead:
        print(MISSING_AS); return 3
    loaded = config_source.load_config(Path.cwd(), args.phase, args.lead, github=args.github, interval_s=args.interval, dry_run=args.dry_run or args.command == "snapshot", no_fetch=args.no_fetch)
    if isinstance(loaded, Err): print(f"{CONSOLE_NAME}: {loaded.error.kind}: {loaded.error.fix}"); return 3
    saved = load_state(loaded.value.repo_root, root_id)
    state = saved.value if isinstance(saved, Ok) else EMPTY
    if args.once or args.headless or args.command == "snapshot":
        lock = acquire_lock(loaded.value.repo_root, root_id)
        if isinstance(lock, Err): return 0
        # Probe the live-holder condition before any sources are read.  The
        # runner then owns a fresh lock for its complete read/action/write tick.
        lock.value.close()
        runner.step(loaded.value, state, False)
        return 0
    return app.run(loaded.value, runner.step)
