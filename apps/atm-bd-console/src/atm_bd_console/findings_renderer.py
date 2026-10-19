"""Adapter for the installed sc_render skill dependency."""
from __future__ import annotations

from typing import Any

from .skill_loader import load_skill


def render_findings(repo_root: str, findings: list[dict[str, Any]]) -> str:
    template = "atm-bd-orchestration/templates/qa-findings.md.j2"
    module, skills = load_skill(repo_root, "atm-beads/scripts/sc_render.py", template)
    return str(module.render(skills / template, {"findings": findings}))
