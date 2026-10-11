"""The sc-compose CLI and Python wheel this package runs are one version: the newest release.

Local only (skipped when CI is set; CI installs the manifest pin). It fails, never skips, when PyPI
cannot be read, so it never passes on a version it did not check. A new sc-compose release fails it
until the pin is bumped and both are upgraded.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

import pytest

import install

pytestmark = pytest.mark.skipif(bool(os.environ.get("CI")), reason="local only: CI installs the manifest pin")

PYPI = "https://pypi.org/pypi/sc-compose/json"


def test_sc_compose_cli_and_wheel_are_the_newest_release():
    pin = install.sc_compose_pin()
    with urllib.request.urlopen(PYPI, timeout=30) as response:
        latest = json.load(response)["info"]["version"]
    cli = subprocess.run(["sc-compose", "--version"], capture_output=True, text=True, check=True).stdout.split()[-1]
    # The scripts run under the python3 on PATH, so that interpreter's wheel is the one that counts.
    wheel = subprocess.run(["python3", "-c", "import importlib.metadata as m; print(m.version('sc-compose'))"],
                           capture_output=True, text=True).stdout.strip() or "not installed"
    assert (pin, cli, wheel) == (latest, latest, latest), (
        f"sc-compose {latest} is the newest release; manifest pin {pin}, CLI {cli}, python3 wheel {wheel}. "
        f"Set `sc-compose == {latest}` in plugins/atm-bd-orchestration/manifest.yaml (requires) and "
        f"SC_COMPOSE_VERSION plus the two asset sha256 values in .github/workflows/tests.yml, then run "
        f"`{install.sc_compose_upgrade(latest)}`.")
