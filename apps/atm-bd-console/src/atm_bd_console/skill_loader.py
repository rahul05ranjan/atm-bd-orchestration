"""Resolve and cache installed or bundled skill scripts for console adapters."""
from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType


@cache
def load_skill(repo_root: str, script: str, required: str = "") -> tuple[ModuleType, Path]:
    root = Path(repo_root)
    candidates = (
        root / ".claude/skills",
        root / "plugins/atm-bd-orchestration/skills",
        Path(__file__).resolve().parents[4] / "plugins/atm-bd-orchestration/skills",
        Path(__file__).resolve().parent / "_skills/skills",
    )
    skills = next((path for path in candidates if (path / script).is_file()
                   and (not required or (path / required).is_file())), None)
    if skills is None:
        raise RuntimeError(f"skill script or required resource is not installed: {script}")
    spec = importlib.util.spec_from_file_location(f"atm_bd_skill_{Path(script).stem}", skills / script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load skill script: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, skills
