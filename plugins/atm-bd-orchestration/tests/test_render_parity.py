"""The `sc-compose render --strict` CLI and the sc-compose wheel render every template the same, and both reject
a template that references a variable it was not given.

Agents render with the CLI (`sc-compose render --strict`), scripts and tests with the wheel; this is the proof they
agree, over every template that has an example vars file. `atm compose` is atm's renderer and is tested in atm-core.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG / "skills/atm-beads/scripts"))
import sc_render  # noqa: E402

CASES = sorted(
    (template, vars_file)
    for template in PKG.glob("skills/*/templates/*.j2")
    for vars_file in [template.parents[1] / "examples" / (template.name.removesuffix(".j2").rsplit(".", 1)[0] + "-vars.json")]
    if vars_file.is_file())


def test_there_are_templates_to_compare():
    assert len(CASES) > 20, CASES


@pytest.mark.parametrize("template,vars_file", [pytest.param(t, v, id=str(t.relative_to(PKG))) for t, v in CASES])
def test_cli_and_wheel_render_the_same(template, vars_file):
    with tempfile.TemporaryDirectory() as scratch:  # the CLI keeps a .sc-compose/ log in its cwd
        cli = subprocess.run(["sc-compose", "render", "--strict", "--root", str(template.parent), "--file", str(template),
                              "--var-file", str(vars_file)], cwd=scratch, capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr
    assert cli.stdout == sc_render.render(template, json.loads(vars_file.read_text()))


@pytest.mark.parametrize("renderer", ["wheel", "sc-compose"])
def test_a_missing_variable_is_rejected_naming_it(renderer, tmp_path):
    template = tmp_path / "missing.md.j2"
    template.write_text("given {{ given }}, missing {{ missing }}\n")
    if renderer == "wheel":
        with pytest.raises(sc_render.RenderError) as exc:
            sc_render.render(template, {"given": "x"})
        returncode, error = 1, sc_render.error_text(exc.value)
    else:
        result = subprocess.run(["sc-compose", "render", "--strict", "--root", str(tmp_path), "--file", str(template),
                                 "--var", "given=x"], cwd=tmp_path, capture_output=True, text=True)
        returncode, error = result.returncode, result.stderr
    assert returncode != 0
    assert "missing" in error and str(template) in error, error
