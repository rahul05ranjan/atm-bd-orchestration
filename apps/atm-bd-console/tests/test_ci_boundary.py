from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "atm_bd_console"


def test_ci_classification_is_independent_of_presentation() -> None:
    for name in ("github_source", "migrations"):
        tree = ast.parse((PACKAGE / f"{name}.py").read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 1]
        assert any(node.module == "ci" and any(alias.name == "ci_failure_key" for alias in node.names)
                   for node in imports)
        assert not any(node.module == "icons" for node in imports)
    tree = ast.parse((PACKAGE / "codec.py").read_text())
    assert not any(isinstance(node, ast.ImportFrom) and node.module in {"ci", "icons"}
                   for node in ast.walk(tree))
    tree = ast.parse((PACKAGE / "ci.py").read_text())
    assert not any(isinstance(node, ast.ImportFrom) and node.module == "icons" for node in ast.walk(tree))
