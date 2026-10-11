"""The sc-compose CLI and Python wheel this package runs are one version, in the manifest's supported range.

It runs in CI too, which installs the floor (SC_COMPOSE_VERSION in .github/workflows/tests.yml), so the floor stays
tested; locally it checks whatever newer release is installed. A new upstream release does not fail it.
"""
from __future__ import annotations

import subprocess

import install


def test_sc_compose_cli_and_wheel_are_one_supported_version():
    floor, ceiling = install.sc_compose_range()
    cli = subprocess.run(["sc-compose", "--version"], capture_output=True, text=True, check=True).stdout.split()[-1]
    # The scripts run under the python3 on PATH, so that interpreter's wheel is the one that counts.
    wheel = subprocess.run(["python3", "-c", "import importlib.metadata as m; print(m.version('sc-compose'))"],
                           capture_output=True, text=True).stdout.strip() or "not installed"
    assert install.sc_compose_supported(cli) and cli == wheel, (
        f"the package needs the sc-compose CLI and python3 wheel at one version >= {floor}, < {ceiling}; "
        f"CLI {cli}, wheel {wheel}. Install {install.sc_compose_upgrade()}.")
