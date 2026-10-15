from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
SOURCES = {"atm_source.py", "beads_source.py", "git_source.py", "github_source.py", "status.py"}
FORBIDDEN_IMPORTERS = {"model.py", "dispatch.py", "render.py", "prs.py"}


def violations(root: Path) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if path.name not in SOURCES and ("import subprocess" in text or "from subprocess" in text):
            found.append(f"{path}: subprocess")
        if path.name in FORBIDDEN_IMPORTERS and "_source" in text:
            found.append(f"{path}: source import")
        if len(text.splitlines()) > 1000:
            found.append(f"{path}: exceeds 1000 lines")
        if "bd list --all" in text:
            found.append(f"{path}: bd list --all")
    return found


def test_source_boundaries() -> None:
    assert violations(ROOT) == []


def test_boundaries_detect_planted_violations(tmp_path: Path) -> None:
    model = tmp_path / "model.py"
    model.write_text("import subprocess\n" + "# line\n" * 1000)
    assert any("subprocess" in item for item in violations(tmp_path))
    assert any("exceeds 1000" in item for item in violations(tmp_path))
