"""Run the skills' own test suites against an installed, rendered copy.

The upstream suites resolve paths from the repository layout (`.claude/skills/...`,
`.claude/skills/atm-bd-orchestration/scripts/jev_client.py`) and some scripts carry install-time values, so they run
inside a throwaway repository installed with a non-default bead prefix (`myp`).
Needs `sc-compose`; skipped (and says so) without it.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import install
from test_install import make_repo

PKG = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("sc-compose") is None, reason="sc-compose not on PATH; skill suites not run")

SUITES = (
    ".claude/skills/atm-bd-orchestration/scripts/tests",
    ".claude/skills/atm-beads/tests",
    ".claude/skills/sprint-report/tests",
    ".claude/skills/prompt-rewrite-judge/scripts/tests",
)


# PATH before any fixture of this module ran: what the installed suites must inherit.
PATH_BEFORE_FIXTURES = os.environ["PATH"]

# Installer-checked CLIs the installed suites never invoke, so a host without them (CI runners) may
# fake them for the install step alone. jq and git are not here: the suites run them for real.
ABSENT_TOOL_FAKES = ("atm", "atm-bd-console", "bd")


@contextlib.contextmanager
def absent_installer_tools_faked(bin_dir: Path):
    """Put version-printing fakes of the ABSENT_TOOL_FAKES CLIs missing from this host first on PATH, and
    restore the real PATH on exit so nothing started afterwards (the installed suites) can see them."""
    bin_dir.mkdir(exist_ok=True)
    floors = dict(install.declared_tool_requirements())
    for name in ABSENT_TOOL_FAKES:
        if shutil.which(name) is None:
            path = bin_dir / name
            path.write_text(f"#!/bin/sh\necho '{name} {floors[name]}'\n")
            path.chmod(0o755)
    original = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{original}"
    try:
        yield
    finally:
        os.environ["PATH"] = original


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    repo = make_repo(tmp_path_factory.mktemp("suites"))
    with absent_installer_tools_faked(tmp_path_factory.mktemp("required-tools")):
        assert install.main(["--dest", str(repo / ".claude"), "--set", "qa_member=quality-mgr"]) == 0
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "install"], check=True)
    return repo


@pytest.mark.parametrize("suite", SUITES)
def test_installed_suite_passes(installed, suite):
    skill_dir = installed / suite
    scripts = skill_dir.parent if skill_dir.parent.name == "scripts" else skill_dir.parent / "scripts"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(scripts), str(scripts.parent)])}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", suite],
                          cwd=installed, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.returncode == 0, proc.stdout[-16000:]   # room for the pour test's server log tails


def test_installed_fixture_does_not_leak_installer_tool_shims(installed):
    """Installed skill suites inherit the real shell tool path, not installer test fakes: the fixture put PATH
    back exactly, so no fake is resolvable afterwards, and the fakes never stand in for jq or git."""
    assert os.environ["PATH"] == PATH_BEFORE_FIXTURES
    for name in ABSENT_TOOL_FAKES:
        found = shutil.which(name)
        assert found is None or not Path(found).parent.name.startswith("required-tools"), found
    assert not {"jq", "git"} & set(ABSENT_TOOL_FAKES)
    jq = shutil.which("jq")
    assert jq is not None and not Path(jq).parent.name.startswith("required-tools"), jq


def test_dev_bead_ids_take_the_installed_prefix(installed):
    """The sprint index derives dev bead ids with the repository's bead prefix, not the source repository's."""
    plan = installed / "docs/plans/phase-x.jsonl"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text('{"sprint": "x-1"}\n{"sprint": "x-2", "depends_on": ["x-1"]}\n')
    code = ("import json, sys; from pathlib import Path; sys.path.insert(0, '.claude/skills/atm-beads/scripts');"
            "import sprint_index_common as c; print(json.dumps(c.load_phase_plan(Path(sys.argv[1]))))")
    out = subprocess.run([sys.executable, "-c", code, str(plan)], cwd=installed, check=True,
                         stdout=subprocess.PIPE, text=True).stdout
    index = json.loads(out)
    assert index["root_bead_id"] == "myp-phase-x"
    assert [row["sprint_bead_id"] for row in index["sprints"]] == ["myp-x-1", "myp-x-2"]
    assert index["sprints"][1]["depends_on_sanity_bead_ids"] == ["myp-x-1.group-sanity"]


@pytest.mark.parametrize("example", ("sprint-bead-vars-d-4.json", "sprint-bead-vars-d-5.json"))
def test_sprint_bead_template_renders_a_valid_sprint_bead(installed, example):
    """A strict render of the sprint-bead template is a valid SprintBead: a stage:sprint container with a numbered
    Deliverables list and its metadata (difficulty)."""
    skill = installed / ".claude/skills/atm-beads"
    sys.path.insert(0, str(skill / "scripts"))
    import sc_render
    bead = json.loads(sc_render.render(skill / "templates/sprint-bead.json.j2",
                                       json.loads((skill / "examples" / example).read_text())))
    import bead_schema
    assert bead_schema.problems(bead, bead_schema.SprintBead) == []
    assert "stage:sprint" in bead["labels"]
    # a container: not the dev task, and its only edge is to its parent (bead-groups adds the dev-bead edges)
    assert "stage:dev" not in bead["labels"]
    assert [d["type"] for d in bead["dependencies"]] == ["parent-child"]
