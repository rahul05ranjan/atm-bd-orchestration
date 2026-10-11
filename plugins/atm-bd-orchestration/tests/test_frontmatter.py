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

PKG = Path(__file__).resolve().parents[1]
TEMPLATES = sorted(p for p in PKG.rglob("*.j2") if ".sc-compose" not in p.parts)
SCHEMA_CODES = {"ERR_VAL_MISSING_FRONTMATTER", "ERR_VAL_UNDECLARED_TOKEN"}
# sc-compose 1.6.1 reports these Jinja keywords as undeclared tokens (randlee/sc-compose#784).
NOT_VARIABLES = {"autoescape", "endautoescape", "is", "defined"}
# `sc-compose bead render` 1.6.1 copies front matter into the rendered formula, which is then not TOML, so a
# formula cannot carry front matter yet (randlee/sc-compose#785).
FORMULAS = {"skills/atm-bd-orchestration/formulas/finding-group.formula.toml.j2",
            "skills/atm-bd-orchestration/formulas/sprint-group.formula.toml.j2"}


def schema_errors(template: Path, cwd: Path) -> list[str]:
    result = subprocess.run(["sc-compose", "validate", "--strict", "--json", "--root", str(template.parent),
                             "--file", str(template)], cwd=cwd, capture_output=True, text=True)
    diagnostics = json.loads(result.stdout)["diagnostics"]
    return [f"{d['code']}: {d['message']}" for d in diagnostics
            if d["code"] in SCHEMA_CODES
            and d["message"].removeprefix("undeclared referenced token: ") not in NOT_VARIABLES]


def case(template: Path):
    rel = str(template.relative_to(PKG))
    marks = [pytest.mark.xfail(strict=True, reason="randlee/sc-compose#785: bead render keeps front matter")] \
        if rel in FORMULAS else []
    return pytest.param(template, id=rel, marks=marks)


def test_there_are_templates():
    assert len(TEMPLATES) > 30, TEMPLATES
    assert FORMULAS <= {str(t.relative_to(PKG)) for t in TEMPLATES}


@pytest.mark.parametrize("template", [case(t) for t in TEMPLATES])
def test_front_matter_declares_every_variable(template, tmp_path):
    assert schema_errors(template, tmp_path) == []
