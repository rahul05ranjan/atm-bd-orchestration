"""Set package test import paths and run skill suites against an installed copy."""
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
for path in (PKG, PKG / "tests", PKG / "skills/atm-bd-orchestration/scripts/tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

collect_ignore = ["skills", "agents"]
