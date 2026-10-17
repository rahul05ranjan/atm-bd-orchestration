"""Package test setup: put install.py and tests/ on sys.path, and keep pytest out of
skills/ and agents/ (their suites run against an installed copy, from
tests/test_skill_suites.py)."""
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent
for path in (PKG, PKG / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import install

collect_ignore = ["skills", "agents"]


@pytest.fixture(scope="session", autouse=True)
def current_console_tools(tmp_path_factory):
    """All installer tests get current external-tool fakes unless they replace PATH."""
    bin_dir = tmp_path_factory.mktemp("required-tools")
    floors = dict(install.declared_tool_requirements())
    for name in ("atm-bd-console", "atm", "bd", "jq"):
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\necho '{name} {floors[name]}'\n")
        path.chmod(0o755)
    original = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{original}"
    try:
        yield
    finally:
        os.environ["PATH"] = original
