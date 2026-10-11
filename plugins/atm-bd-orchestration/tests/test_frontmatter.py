"""Every template in the package has front matter that declares every variable it references.

Front matter is the template's input schema: `sc-compose validate --strict` fails a template with no front
matter (ERR_VAL_MISSING_FRONTMATTER) or one that references a variable it does not declare
(ERR_VAL_UNDECLARED_TOKEN).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[1]
TEMPLATES = sorted(p for p in PKG.rglob("*.j2") if ".sc-compose" not in p.parts)
SCHEMA_CODES = {"ERR_VAL_MISSING_FRONTMATTER", "ERR_VAL_UNDECLARED_TOKEN"}
# sc-compose 1.7.0 reports these Jinja keywords as undeclared tokens (randlee/sc-compose#784).
NOT_VARIABLES = {"autoescape", "endautoescape", "is", "defined"}
# The bead formulas, rendered by `sc-compose bead attach`, carry front matter like every other template.
FORMULAS = {"skills/atm-bd-orchestration/formulas/finding-group.formula.toml.j2",
            "skills/atm-bd-orchestration/formulas/sprint-group.formula.toml.j2"}


def schema_errors(template: Path, cwd: Path) -> list[str]:
    result = subprocess.run(["sc-compose", "validate", "--strict", "--json", "--root", str(template.parent),
                             "--file", str(template)], cwd=cwd, capture_output=True, text=True)
    diagnostics = json.loads(result.stdout)["diagnostics"]
    return [f"{d['code']}: {d['message']}" for d in diagnostics
            if d["code"] in SCHEMA_CODES
            and d["message"].removeprefix("undeclared referenced token: ") not in NOT_VARIABLES]


def test_there_are_templates():
    assert len(TEMPLATES) > 30, TEMPLATES
    assert FORMULAS <= {str(t.relative_to(PKG)) for t in TEMPLATES}


@pytest.mark.parametrize("template", [pytest.param(t, id=str(t.relative_to(PKG))) for t in TEMPLATES])
def test_front_matter_declares_every_variable(template, tmp_path):
    assert schema_errors(template, tmp_path) == []



def bead_render(formula: str, tmp_path: Path, planted: str = "", extra: dict | None = None):
    """`sc-compose bead render` of a package formula, with an optional planted line, given every variable its
    front matter requires (lists for the ones it iterates)."""
    source = (PKG / formula).read_text()
    front = yaml.safe_load(source.split("---\n", 2)[1])
    (tmp_path / "f.toml.j2").write_text(source + planted)
    variables = {name: ["x"] if name in ("requirements", "adrs") else "x" for name in front["required_variables"]}
    request = {"schema": "sc-compose/beads/v1", "operation": "render", "working_directory": str(tmp_path),
               "template": "f.toml.j2", "rendered_formula": "f.formula.toml", "bead_variables": {},
               "compose_variables": {**variables, **(extra or {})}}
    (tmp_path / "r.json").write_text(json.dumps(request))
    return subprocess.run(["sc-compose", "bead", "render", "--request", "r.json", "--json"], cwd=tmp_path,
                          capture_output=True, text=True)


@pytest.mark.parametrize("formula", sorted(FORMULAS))
def test_a_formula_renders_with_the_variables_its_front_matter_requires(formula, tmp_path):
    result = bead_render(formula, tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.xfail(strict=True, reason="randlee/sc-compose#791: bead render accepts a supplied variable the front matter "
                                       "does not declare, and validate cannot read {{{ }}}")
@pytest.mark.parametrize("formula", sorted(FORMULAS))
def test_a_formula_variable_the_front_matter_does_not_declare_is_refused(formula, tmp_path):
    result = bead_render(formula, tmp_path, "\n# undeclared: {{{ nobody }}}\n", {"nobody": "x"})
    assert result.returncode != 0, result.stdout
