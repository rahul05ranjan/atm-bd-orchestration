"""Adapter for the installed sc_render skill dependency."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def render_findings(repo_root: str, findings: list[dict[str, Any]]) -> str:
    root = Path(repo_root)
    installed = root / ".claude" / "skills"
    source = root / "plugins" / "atm-bd-orchestration" / "skills"
    bundled = Path(__file__).resolve().parent / "_skills" / "skills"
    skills = next((path for path in (installed, source, bundled)
                   if (path / "atm-beads/scripts/sc_render.py").is_file()
                   and (path / "atm-bd-orchestration/templates/qa-findings.md.j2").is_file()), None)
    if skills is None:
        raise RuntimeError("QA findings template or sc_render is not installed")
    script = skills / "atm-beads/scripts/sc_render.py"
    spec = importlib.util.spec_from_file_location("atm_bd_sc_render", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sc_render")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.render(skills / "atm-bd-orchestration/templates/qa-findings.md.j2", {"findings": findings}))
