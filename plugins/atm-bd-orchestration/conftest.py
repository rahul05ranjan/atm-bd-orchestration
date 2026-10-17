"""Package test setup: put install.py and tests/ on sys.path, and keep pytest out of
skills/ and agents/ (their suites run against an installed copy, from
tests/test_skill_suites.py)."""
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
for path in (PKG, PKG / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

collect_ignore = ["skills", "agents"]
