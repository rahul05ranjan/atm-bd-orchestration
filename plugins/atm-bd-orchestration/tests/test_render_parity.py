"""`atm compose`, the `sc-compose render` CLI and the sc-compose wheel render every template the same.

Agents render with `atm compose`, scripts with the wheel, and tests with sc-compose; this is the
proof they agree, over every template that has an example vars file. Local only (skipped when CI is
set; CI does not install atm).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG / "skills/atm-beads/scripts"))
import sc_render  # noqa: E402

pytestmark = pytest.mark.skipif(bool(os.environ.get("CI")), reason="local only: CI does not install atm")

CASES = sorted(
    (template, vars_file)
    for template in PKG.glob("skills/*/templates/*.j2")
    for vars_file in [template.parents[1] / "examples" / (template.name.removesuffix(".j2").rsplit(".", 1)[0] + "-vars.json")]
    if vars_file.is_file())


def test_there_are_templates_to_compare():
    assert len(CASES) > 20, CASES


# atm 1.6.1 renders a boolean nested in a list item as `False` where sc-compose renders `false`
# (atm-core issue: atm compose and sc-compose render differently).
ATM_BOOL_CASE = "skills/atm-bd-orchestration/templates/dev-sanity-complete.md.j2"


def case(template, vars_file):
    marks = [pytest.mark.xfail(strict=True, reason="atm compose renders a nested boolean as False")] \
        if str(template.relative_to(PKG)) == ATM_BOOL_CASE else []
    return pytest.param(template, vars_file, id=str(template.relative_to(PKG)), marks=marks)


@pytest.mark.parametrize("template,vars_file", [case(t, v) for t, v in CASES])
def test_atm_compose_cli_and_wheel_render_the_same(template, vars_file):
    with tempfile.TemporaryDirectory() as scratch:  # both CLIs keep a .sc-compose/ log in their cwd
        atm = subprocess.run(["atm", "compose", "--template", str(template), "--vars", str(vars_file)],
                             cwd=scratch, capture_output=True, text=True)
        cli = subprocess.run(["sc-compose", "render", "--root", str(template.parent), "--file", str(template),
                              "--var-file", str(vars_file)], cwd=scratch, capture_output=True, text=True)
    assert atm.returncode == 0, atm.stderr
    assert cli.returncode == 0, cli.stderr
    wheel = sc_render.render(template, json.loads(vars_file.read_text()), strict=False)
    assert atm.stdout == cli.stdout == wheel
