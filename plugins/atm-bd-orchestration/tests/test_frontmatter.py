"""Every template in the package has front matter that declares every variable it references.

Front matter is the template's input schema: `sc-compose validate --strict` fails a template with no front
matter (ERR_VAL_MISSING_FRONTMATTER) or one that references a variable it does not declare
(ERR_VAL_UNDECLARED_TOKEN).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[1]
TEMPLATES = sorted(p for p in PKG.rglob("*.j2") if ".sc-compose" not in p.parts)
SCHEMA_CODES = {"ERR_VAL_MISSING_FRONTMATTER", "ERR_VAL_UNDECLARED_TOKEN"}
# sc-compose 1.7.0 reports these Jinja keywords as undeclared tokens (randlee/sc-compose#784).
NOT_VARIABLES = {"autoescape", "endautoescape", "is", "defined"}
JINJA_WORDS = {"for", "in", "if", "elif", "else", "endif", "endfor", "not", "and", "or", "is", "loop", "true", "false",
               "none", "True", "False", "None"}
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


def formula_schema_errors(formula: Path) -> list[str]:
    """sc-compose#791 workaround; delete when `sc-compose bead render` enforces front matter.

    `sc-compose validate` cannot read `{{{ }}}` and bead render 1.7.0 accepts a supplied variable the front
    matter does not declare, so compare the formula's variables (the root names of its `{{{ }}}` tokens and of
    its `{% %}` statements, outside `{# #}` comments, less loop variables) with its required_variables and
    defaults: an undeclared variable or a declared one it does not use is an error naming both."""
    source = formula.read_text()
    _, front, body = source.split("---\n", 2)
    front = yaml.safe_load(front)
    declared = set(front.get("required_variables", [])) | set(front.get("defaults") or {})
    body = re.sub(r"\{#.*?#\}", "", body, flags=re.S)
    used = {re.match(r"\s*([A-Za-z_]\w*)", expr).group(1) for expr in re.findall(r"\{\{\{(.*?)\}\}\}", body, flags=re.S)}
    bound = set()
    for statement in re.findall(r"\{%-?(.*?)-?%\}", body, flags=re.S):
        statement = re.sub(r"\"[^\"]*\"|'[^']*'", "", statement)
        loop = re.match(r"\s*for\s+(\w+)\s+in\b", statement)
        if loop:
            bound.add(loop.group(1))
            statement = statement[loop.end():]
        used |= {name for name in re.findall(r"(?<![.\w])([A-Za-z_]\w*)", statement) if name not in JINJA_WORDS}
    used -= bound
    return [f"{formula.name}: {name} is used but not declared in its front matter" for name in sorted(used - declared)] + \
        [f"{formula.name}: {name} is declared in its front matter but not used" for name in sorted(declared - used)]


@pytest.mark.parametrize("formula", sorted(PKG.rglob("*.formula.toml.j2")), ids=lambda p: str(p.relative_to(PKG)))
def test_a_formula_uses_exactly_the_variables_its_front_matter_declares(formula):
    assert formula_schema_errors(formula) == []
