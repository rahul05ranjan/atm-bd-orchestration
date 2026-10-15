"""Command-line entry point for one console phase."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import app, runner
from . import config as config_source
from .result import Err
from .state import load_state
from .types import State

MISSING_AS = """atm-bd-console: --as <phase lead> is required: the ATM identity that leads this phase.
Tasks are assigned as that identity, and escalations go to it when no escalation recipient is set.
The phase lead varies by phase, so it is never inferred from $ATM_IDENTITY or the repository's lead.
Example: atm-bd-console --phase <x> --as fenix"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atm-bd-console")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--as", dest="lead")
    parser.add_argument("--github", choices=("graphql", "rest", "off"))
    parser.add_argument("--interval", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    if not args.lead:
        print(MISSING_AS)
        return 3
    loaded = config_source.load_config(Path.cwd(), args.phase, args.lead, github=args.github,
                                       interval_s=args.interval, dry_run=args.dry_run, no_fetch=args.no_fetch)
    if isinstance(loaded, Err):
        print(f"atm-bd-console: {loaded.error.kind}: {loaded.error.fix}")
        return 3
    state = load_state(loaded.value.repo_root, f"atm-phase-{args.phase}")
    current = state.value if not isinstance(state, Err) else State((), None, None, None, None, (), (), {})
    if args.once or args.headless:
        runner.step(loaded.value, current, False)
        return 0
    app.run(loaded.value, runner.step)
    return 0
