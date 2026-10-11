"""Package consistency and installer tests.

Run from the package root: `python3 -m pytest -q` (conftest.py sets the path).
The install tests need `sc-compose` on PATH; they are skipped (and say so) without it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import io
from pathlib import Path

import pytest
import yaml

import gen_manifest
import install

PKG = Path(__file__).resolve().parents[1]
SC_COMPOSE = shutil.which("sc-compose") is not None
REPO_STRINGS = ("sc-observability", "sc-obs", "obs-", "randlee/sc-", "/Users/randlee", "atm-core", "atm-dev")
SPEC_VARIABLES = [
    "bead_prefix", "lead", "dev_sanity_member", "qa_member", "worktree_base", "test_command",
    "lint_command", "integration_branch_pattern", "plans_dir", "requirements_globs", "adr_globs",
    "policy_path", "reviewers_round1",
]
needs_sc_compose = pytest.mark.skipif(not SC_COMPOSE, reason="sc-compose not on PATH; install not verified")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- consistency


def test_generated_blocks_are_current():
    assert gen_manifest.main(["--check"]) == 0


def test_manifest_lists_every_skill_and_agent_file():
    assert install.load_manifest_artifacts(PKG) == gen_manifest.tree_artifacts()


def test_render_list_covers_exactly_the_placeholder_files():
    renders = install.load_registry(PKG)["render"]
    assert renders == gen_manifest.render_list(install.load_manifest_artifacts(PKG))
    assert not [r for r in renders if r.endswith(".j2")], "dispatch templates are never rendered at install"
    for rel in renders:
        assert install.PLACEHOLDER_RE.search((PKG / rel).read_text()), rel


def test_no_repo_or_team_specific_strings_in_sources():
    hits = []
    for cat in ("skills", "agents", "config"):
        for path in (PKG / cat).rglob("*"):
            if not path.is_file() or {"node_modules", "__pycache__", ".sc-compose"} & set(path.parts):  # .sc-compose/: gitignored render logs
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for needle in REPO_STRINGS:
                if needle in text:
                    hits.append(f"{path.relative_to(PKG)}: {needle}")
    assert hits == [], "\n".join(hits)


def test_workflow_issue_bead_declares_parent():
    text = (PKG / "skills/atm-bd-orchestration/templates/workflow-issue-bead.json.j2").read_text()
    assert "  - parent\n" in text and "{{ parent | tojson }}" in text


def test_config_template_declares_exactly_the_spec_variables_without_defaults():
    assert install.config_variables(PKG) == SPEC_VARIABLES
    front = (PKG / install.CONFIG_TEMPLATE).read_text().split("---\n")[1]
    assert "default" not in front
    assert set(install.ROLE_KEYS) | install.LIST_VARIABLES <= set(SPEC_VARIABLES)


def test_versions_agree():
    version = install.package_version(PKG)
    assert version == "0.14.0"
    assert json.loads((PKG / ".claude-plugin/plugin.json").read_text())["version"] == version
    assert f"## [{version}]" in (PKG / "CHANGELOG.md").read_text()



def shim_sc_compose(bin_dir: Path, cli: str, wheel: str) -> None:
    """`sc-compose --version` prints `sc-compose <cli>`; `python3 -c ...` prints the wheel version <wheel>."""
    bin_dir.mkdir(exist_ok=True)
    for name, out in (("sc-compose", f"sc-compose {cli}"), ("python3", wheel)):
        (bin_dir / name).write_text(f"#!/bin/sh\necho '{out}'\n")
        (bin_dir / name).chmod(0o755)


@pytest.mark.parametrize("cli, wheel, refusal", [
    ("1.7.0", "1.7.0", None),
    ("1.7.1", "1.7.1", None),
    ("1.12.0", "1.12.0", None),
    ("1.6.1", "1.6.1", "sc-compose CLI is sc-compose 1.6.1, the package needs >= 1.7.0, < 2"),
    ("2.0.0", "2.0.0", "sc-compose CLI is sc-compose 2.0.0, the package needs >= 1.7.0, < 2"),
    ("1.7.0", "1.6.1", "python3 on PATH has the sc-compose wheel 1.6.1, the package needs >= 1.7.0, < 2"),
    ("1.7.1", "1.7.0", "the sc-compose CLI is 1.7.1 and the python3 wheel is 1.7.0; they must be the same version"),
])
def test_check_tools_accepts_one_sc_compose_version_in_the_range(tmp_path, monkeypatch, cli, wheel, refusal):
    shim_sc_compose(tmp_path / "bin", cli, wheel)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    if refusal is None:
        install.check_tools()
        return
    with pytest.raises(install.InstallError) as caught:
        install.check_tools()
    assert refusal in str(caught.value)
    assert "python3 -m pip install 'sc-compose>=1.7.0,<2'" in str(caught.value)


def test_the_manifest_requires_sc_compose_from_1_7_0_below_2():
    assert install.sc_compose_range() == ("1.7.0", 2)


VERSIONED_SKILLS = sorted(p for p in PKG.glob("skills/*/SKILL.md")
                          if "version" in yaml.safe_load(p.read_text().split("---\n", 2)[1]))


def test_some_skills_carry_a_version_marker():
    assert len(VERSIONED_SKILLS) >= 2, VERSIONED_SKILLS


@pytest.mark.parametrize("skill", VERSIONED_SKILLS, ids=lambda p: p.parent.name)
def test_a_skill_version_marker_is_the_package_version(skill):
    """Drift audits read SKILL.md `version:`; it must be the version plugin.json ships."""
    front = yaml.safe_load(skill.read_text().split("---\n", 2)[1])
    assert str(front["version"]) == json.loads((PKG / ".claude-plugin/plugin.json").read_text())["version"]


def test_legacy_owned_table_is_frozen_at_0_2_3():
    data = json.loads((PKG / install.LEGACY_OWNED).read_text())
    assert data["through"] == "d5b7301"
    files = data["files"]
    assert "skills/atm-beads/scripts/validate-plan" in files and "assets/scripts/jev_client.py" in files
    assert all(shas and all(len(s) == 64 for s in shas) for shas in files.values())


# ---------------------------------------------------------------- fixtures


REGISTRY = """\
# consumer registry: comments and other keys survive the roles write
agents:
  quality-mgr:
    path: .claude/agents/quality-mgr.md
bead_prefix: myp
worktree_base: {worktree_base}
test_command: just test
lint_command: just lint
integration_branch_pattern: integrate/phase-{{phase}}
plans_dir: docs/plans
requirements_globs: [docs/requirements.md, "docs/**/requirements.md"]
adr_globs: ["docs/adr/*.md"]
policy_path: .claude/project/quality-policy.md
reviewers_round1: [req-qa, arch-qa]

roles:
  dev-sanity: my-sanity   # the member running the dev-sanity directive
  lead: my-lead
skills:
  atm-beads:
"""
AGENTS = ("quality-mgr", "req-qa", "arch-qa", "ruthless-boundary-qa", "my-sanity")


def make_repo(tmp_path: Path, *, drop: tuple = (), agents: tuple = AGENTS, dolt_mode: str = "server") -> Path:
    repo = tmp_path / "my-repo"
    (repo / ".claude" / "agents").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:owner/my-repo.git"], check=True)
    text = REGISTRY.format(worktree_base=repo.resolve().parent / "my-repo-worktrees")
    lines = [line for line in text.splitlines(keepends=True) if not any(line.startswith(f"{d}:") for d in drop)]
    (repo / ".claude/agents/registry.yaml").write_text("".join(lines))
    for name in agents:
        (repo / ".claude/agents" / f"{name}.md").write_text(f"---\nname: {name}\n---\n")
    (repo / ".beads").mkdir()
    (repo / ".beads/metadata.json").write_text(json.dumps({"backend": "dolt", "dolt_mode": dolt_mode}))
    return repo


QA = ["--set", "qa_member=quality-mgr"]


def run(repo: Path, *extra: str, target: str = ".claude", pkg: Path = PKG, capsys=None):
    rc = install.main(["--dest", str(repo / target), *extra], pkg_dir=pkg)
    err = capsys.readouterr().err if capsys else ""
    return rc, err


def snapshot(repo: Path) -> dict:
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(repo).parts}


def lock(repo: Path) -> dict:
    return json.loads((repo / install.LOCK_OUT).read_text())


@pytest.fixture
def pkg_copy(tmp_path):
    dst = tmp_path / "pkg"
    shutil.copytree(PKG, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules"))
    return dst


def regenerate(pkg: Path) -> None:
    subprocess.run(["python3", str(pkg / "tests/gen_manifest.py")], check=True, stdout=subprocess.DEVNULL)


# ---------------------------------------------------------------- values (no sc-compose)


def test_unknown_set_key_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(install.InstallError, match=r"unknown --set variable\(s\): bogus, team"):
        install.resolve_config(repo, {"bogus": "1", "team": "x", "test_command": "make"})


def test_set_wins_and_lists_parse(tmp_path):
    repo = make_repo(tmp_path)
    vals = install.resolve_config(repo, {"test_command": "make test", "reviewers_round1": "a, b",
                                         "adr_globs": '["x/*.md"]', "lead": "other"})
    assert vals["test_command"] == "make test" and vals["lead"] == "other"
    assert vals["reviewers_round1"] == ["a", "b"] and vals["adr_globs"] == ["x/*.md"]
    assert vals["dev_sanity_member"] == "my-sanity" and "qa_member" not in vals


@pytest.mark.parametrize("removed", ["reviewers_fix_round", "reviewers_scope_locked"])
def test_removed_reviewer_lists_are_ignored_in_registry_and_rejected_by_set(tmp_path, removed):
    """0.5.0 dropped reviewers_fix_round and reviewers_scope_locked: a leftover registry key is not read; --set names it unknown."""
    repo = make_repo(tmp_path)
    reg = repo / ".claude/agents/registry.yaml"
    reg.write_text(reg.read_text() + f"{removed}: [req-qa]\n")
    assert removed not in install.resolve_config(repo, {})
    with pytest.raises(install.InstallError, match=f"unknown --set variable\\(s\\): {removed}"):
        install.resolve_config(repo, {removed: "req-qa"})


def test_wrong_type_and_misplaced_role_are_named(tmp_path):
    repo = make_repo(tmp_path)
    reg = repo / ".claude/agents/registry.yaml"
    reg.write_text(reg.read_text().replace("reviewers_round1: [req-qa, arch-qa]", "reviewers_round1: req-qa"))
    with pytest.raises(install.InstallError, match="reviewers_round1 must be a list"):
        install.resolve_config(repo, {})
    reg.write_text(reg.read_text() + "lead: x\n")
    with pytest.raises(install.InstallError, match="lead belongs under roles.lead"):
        install.resolve_config(repo, {})


def test_write_roles_updates_in_place_and_keeps_comments(tmp_path):
    reg = tmp_path / "registry.yaml"
    reg.write_text("# top\nroles:\n    lead: old   # keep me\n    dev-sanity: s\n\nskills:\n  a:\n")
    assert install.write_roles(reg, {"lead": "new", "dev-sanity": "s", "quality-mgr": "q"})
    assert reg.read_text() == "# top\nroles:\n    lead: new   # keep me\n    dev-sanity: s\n    quality-mgr: q\n\nskills:\n  a:\n"
    assert not install.write_roles(reg, {"lead": "new", "dev-sanity": "s", "quality-mgr": "q"})
    fresh = tmp_path / "none.yaml"
    fresh.write_text("bead_prefix: x")
    install.write_roles(fresh, {"lead": "l"})
    assert yaml.safe_load(fresh.read_text()) == {"bead_prefix": "x", "roles": {"lead": "l"}}


def test_dest_must_be_a_claude_or_codex_dir(tmp_path):
    with pytest.raises(install.InstallError, match="--dest must be"):
        install.repo_root_for(tmp_path / "somewhere")


# ---------------------------------------------------------------- fresh install


@needs_sc_compose
def test_fresh_install(tmp_path, capsys):
    repo = make_repo(tmp_path)
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 0, err
    dest = repo / ".claude"
    artifacts = install.load_manifest_artifacts(PKG)
    for rel in artifacts["skills"] + artifacts["agents"]:
        assert (dest / rel).is_file(), rel
        assert not install.PLACEHOLDER_RE.search((dest / rel).read_text(errors="ignore")), rel
    # the config file: every declared variable, from registry.yaml and --set
    config = yaml.safe_load((repo / install.CONFIG_OUT).read_text())
    assert list(config) == SPEC_VARIABLES
    assert config["qa_member"] == "quality-mgr" and config["lead"] == "my-lead"
    assert config["integration_branch_pattern"] == "integrate/phase-{phase}"
    assert config["requirements_globs"] == ["docs/requirements.md", "docs/**/requirements.md"]
    assert config["reviewers_round1"] == ["req-qa", "arch-qa"]
    # the install record: version and the sha256 of every file written
    record = lock(repo)
    assert record["package"] == "atm-bd-orchestration" and record["version"] == "0.14.0"
    expected = {f".claude/{rel}" for rel in artifacts["skills"] + artifacts["agents"]} | {install.CONFIG_OUT}
    assert set(record["files"]) == expected
    assert all(sha((repo / k).read_bytes()) == v for k, v in record["files"].items())
    # roles written, the rest of registry.yaml kept
    reg_text = (repo / ".claude/agents/registry.yaml").read_text()
    reg = yaml.safe_load(reg_text)
    assert reg["roles"] == {"dev-sanity": "my-sanity", "lead": "my-lead", "quality-mgr": "quality-mgr"}
    assert "# the member running the dev-sanity directive" in reg_text and "skills" in reg
    # rendered values, the Jev transport, executable scripts, byte-identical templates
    role = (dest / "agents/dev-sanity.md").read_text()
    assert role.startswith("---\nname: dev-sanity\n")
    assert "| Setting | Where | my-repo |" in role and "`my-sanity`" in role
    example = (dest / "skills/atm-bd-orchestration/examples/dev-sanity-template-vars.json").read_text()
    assert "https://github.com/owner/my-repo/pull/" in example and str(repo.resolve().parent / "my-repo-worktrees") in example
    assert '"parent": "myp-workflow-issues"' in (dest / "skills/atm-bd-orchestration/examples/workflow-issue-bead-vars.json").read_text()
    assert (dest / "skills/atm-bd-orchestration/scripts/jev_client.py").read_bytes() == \
        (PKG / "skills/atm-bd-orchestration/scripts/jev_client.py").read_bytes()
    assert not (repo / "scripts").exists()
    assert (dest / "skills/atm-beads/scripts/validate-plan").stat().st_mode & 0o111
    assert (dest / "skills/atm-bd-orchestration/templates/qa-template.xml.j2").read_bytes() == \
        (PKG / "skills/atm-bd-orchestration/templates/qa-template.xml.j2").read_bytes()


@needs_sc_compose
def test_rerun_is_idempotent(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    before = snapshot(repo)
    assert run(repo)[0] == 0   # qa_member is now in roles.quality-mgr
    assert snapshot(repo) == before


# ---------------------------------------------------------------- refusals (nothing written)


@needs_sc_compose
def test_missing_variable_fails_naming_it(tmp_path, capsys):
    repo = make_repo(tmp_path, drop=("test_command", "adr_globs"))
    before = snapshot(repo)
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 1
    assert "missing required variable(s): " in err and "test_command" in err and "adr_globs" in err
    assert ".claude/agents/registry.yaml test_command (or --set test_command=...)" in err
    assert snapshot(repo) == before


@needs_sc_compose
def test_missing_role_variable_names_the_roles_key(tmp_path, capsys):
    repo = make_repo(tmp_path)
    rc, err = run(repo, capsys=capsys)   # no roles.quality-mgr and no --set qa_member
    assert rc == 1 and "qa_member in .claude/agents/registry.yaml roles.quality-mgr" in err


@needs_sc_compose
def test_unknown_set_fails_the_install(tmp_path, capsys):
    repo = make_repo(tmp_path)
    rc, err = run(repo, *QA, "--set", "bogus_var=1", capsys=capsys)
    assert rc == 1 and "unknown --set variable(s): bogus_var" in err
    assert not (repo / install.LOCK_OUT).exists()


@needs_sc_compose
def test_missing_reviewer_agent_fails(tmp_path, capsys):
    # no agent file for the reviewer arch-qa nor for the dev-sanity member my-sanity:
    # only the reviewer fails
    repo = make_repo(tmp_path, agents=("quality-mgr", "req-qa"))
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 1
    assert "no .claude/agents/<name>.md for: arch-qa (reviewers_round1)" in err
    assert "dev_sanity_member" not in err
    assert not (repo / ".claude/skills").exists()


@needs_sc_compose
def test_agent_shipped_by_the_package_counts(tmp_path, capsys):
    repo = make_repo(tmp_path, agents=("quality-mgr", "req-qa", "arch-qa", "ruthless-boundary-qa"))
    rc, err = run(repo, *QA, "--set", "dev_sanity_member=dev-sanity", capsys=capsys)
    assert rc == 0, err


@needs_sc_compose
def test_dev_sanity_member_without_an_agent_file_installs(tmp_path, capsys):
    # roles.dev-sanity names a team member (atm-core: atm-sanity), not an agent file
    repo = make_repo(tmp_path, agents=("quality-mgr", "req-qa", "arch-qa"))
    rc, err = run(repo, *QA, "--set", "dev_sanity_member=atm-sanity", capsys=capsys)
    assert rc == 0, err
    assert not (repo / ".claude/agents/atm-sanity.md").exists()
    assert "atm-sanity" in (repo / install.CONFIG_OUT).read_text()


@needs_sc_compose
@pytest.mark.parametrize("mode", ["embedded", None])
def test_non_server_beads_fails(tmp_path, capsys, mode):
    repo = make_repo(tmp_path, dolt_mode=mode or "server")
    if mode is None:
        (repo / ".beads/metadata.json").unlink()
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 1 and "BEADS_NOT_SERVER_MODE" in err
    assert (f"dolt_mode '{mode}', not 'server'" in err) if mode else ("metadata.json not found" in err)
    assert not (repo / ".claude/skills").exists()


@needs_sc_compose
def test_foreign_skill_directory_fails(tmp_path, capsys):
    repo = make_repo(tmp_path)
    own = repo / ".claude/skills/sprint-report"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("---\nname: sprint-report\n---\nthe repository's own skill\n")
    (own / "report-detailed.md.j2").write_text("mine\n")
    before = snapshot(repo)
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 1
    assert "skill 'sprint-report' exists at .claude/skills/sprint-report and is not owned by atm-bd-orchestration" in err
    assert "sprint-report/SKILL.md" not in err   # one message per foreign directory
    assert snapshot(repo) == before


@needs_sc_compose
def test_foreign_file_at_a_shipped_path_fails(tmp_path, capsys):
    repo = make_repo(tmp_path)
    (repo / ".claude/agents/dev-sanity.md").write_text("the repository's own agent\n")
    rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 1 and ".claude/agents/dev-sanity.md exists and is not owned by atm-bd-orchestration" in err
    assert "rerun with --overwrite" in err
    assert (repo / ".claude/agents/dev-sanity.md").read_text() == "the repository's own agent\n"
    rc, err = run(repo, *QA, "--overwrite", capsys=capsys)
    assert rc == 0, err
    assert "warning: .claude/agents/dev-sanity.md" in err
    [backup] = [p for p in (repo / ".backup").iterdir() if p.name != ".gitignore"]
    assert (backup / ".claude/agents/dev-sanity.md").read_text() == "the repository's own agent\n"
    assert (repo / ".backup/.gitignore").read_text() == "*\n"
    assert lock(repo)["files"][".claude/agents/dev-sanity.md"] == sha((repo / ".claude/agents/dev-sanity.md").read_bytes())


# ---------------------------------------------------------------- re-install / upgrade


@needs_sc_compose
def test_reinstall_updates_unmodified_files_and_rerenders(tmp_path, pkg_copy, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    unrelated = repo / ".claude/skills/atm-beads/notes-of-my-own.md"
    unrelated.write_text("not the package's\n")
    # a new package version changes a copied file and a rendered file
    script = "skills/atm-beads/scripts/resolve-role"
    (pkg_copy / script).write_text((pkg_copy / script).read_text() + "# upgraded\n")
    (pkg_copy / "agents/dev-sanity.md").write_text(
        (pkg_copy / "agents/dev-sanity.md").read_text() + "\nupgraded for {{ dev_sanity_member }}\n")
    # and the repository renames its dev-sanity member
    reg = repo / ".claude/agents/registry.yaml"
    reg.write_text(reg.read_text().replace("dev-sanity: my-sanity", "dev-sanity: quality-mgr"))
    rc, err = run(repo, pkg=pkg_copy, capsys=capsys)
    assert rc == 0, err
    assert (repo / ".claude" / script).read_text().endswith("# upgraded\n")
    assert (repo / ".claude" / script).stat().st_mode & 0o111
    role = (repo / ".claude/agents/dev-sanity.md").read_text()
    assert role.endswith("upgraded for quality-mgr\n") and "`my-sanity`" not in role
    assert yaml.safe_load((repo / install.CONFIG_OUT).read_text())["dev_sanity_member"] == "quality-mgr"
    assert lock(repo)["files"][f".claude/{script}"] == sha((repo / ".claude" / script).read_bytes())
    assert unrelated.read_text() == "not the package's\n"


@needs_sc_compose
def test_reinstall_fails_naming_modified_files_and_changes_nothing(tmp_path, pkg_copy, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    edited = [".claude/skills/atm-beads/SKILL.md", install.CONFIG_OUT]
    for key in edited:
        (repo / key).write_text((repo / key).read_text() + "local edit\n")
    script = "skills/atm-beads/scripts/resolve-role"
    (pkg_copy / script).write_text((pkg_copy / script).read_text() + "# upgraded\n")
    before = snapshot(repo)
    rc, err = run(repo, pkg=pkg_copy, capsys=capsys)
    assert rc == 1
    for key in edited:
        assert f"{key} was modified since atm-bd-orchestration installed it" in err
    assert snapshot(repo) == before   # the unmodified upgrade target was not touched either
    rc, err = run(repo, "--overwrite", pkg=pkg_copy, capsys=capsys)
    assert rc == 0, err
    [backup] = [p for p in (repo / ".backup").iterdir() if p.name != ".gitignore"]
    for key in edited:
        assert (backup / key).read_bytes() == before[key]
        assert not (repo / key).read_text().endswith("local edit\n")
    assert (repo / ".claude" / script).read_text().endswith("# upgraded\n")


@needs_sc_compose
def test_files_no_longer_shipped_are_removed_when_unchanged(tmp_path, pkg_copy, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    gone = ["skills/sprint-review/SKILL.md", "skills/sprint-review/scripts/sprint-review", "agents/sc-sanity-jev.md"]
    for rel in gone:
        (pkg_copy / rel).unlink()
    shutil.rmtree(pkg_copy / "skills/sprint-review")
    regenerate(pkg_copy)
    (repo / ".claude/agents/sc-sanity-jev.md").write_text("edited\n")
    rc, err = run(repo, pkg=pkg_copy, capsys=capsys)
    assert rc == 1 and ".claude/agents/sc-sanity-jev.md was modified" in err and "no longer ships it" in err
    rc, err = run(repo, "--overwrite", pkg=pkg_copy, capsys=capsys)
    assert rc == 0, err
    [backup] = [p for p in (repo / ".backup").iterdir() if p.name != ".gitignore"]
    assert (backup / ".claude/agents/sc-sanity-jev.md").read_text() == "edited\n"
    assert not (repo / ".claude/agents/sc-sanity-jev.md").exists()
    assert ".claude/agents/sc-sanity-jev.md" not in lock(repo)["files"]
    assert not (repo / ".claude/skills/sprint-review").exists()
    assert not any(k.startswith(".claude/skills/sprint-review/") for k in lock(repo)["files"])


@needs_sc_compose
def test_upgrade_removes_the_repo_root_jev_client_0_10_4_placed(tmp_path, capsys):
    # 0.10.4 placed the Jev transport at <repo>/scripts/jev_client.py and recorded it in the lock
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    old = (PKG / "skills/atm-bd-orchestration/scripts/jev_client.py").read_bytes()
    (repo / "scripts").mkdir()
    (repo / "scripts/jev_client.py").write_bytes(old)
    record = lock(repo)
    record["files"]["scripts/jev_client.py"] = sha(old)
    (repo / install.LOCK_OUT).write_text(json.dumps(record))
    rc, err = run(repo, capsys=capsys)
    assert rc == 0, err
    assert not (repo / "scripts").exists() and "scripts/jev_client.py" not in lock(repo)["files"]
    assert (repo / ".claude/skills/atm-bd-orchestration/scripts/jev_client.py").read_bytes() == old


@needs_sc_compose
def test_upgrade_refuses_a_modified_repo_root_jev_client_and_backs_it_up_with_overwrite(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    old = (PKG / "skills/atm-bd-orchestration/scripts/jev_client.py").read_bytes()
    (repo / "scripts").mkdir()
    (repo / "scripts/jev_client.py").write_bytes(b"edited\n")
    (repo / "scripts/other.sh").write_text("the repository's own script\n")
    record = lock(repo)
    record["files"]["scripts/jev_client.py"] = sha(old)
    (repo / install.LOCK_OUT).write_text(json.dumps(record))
    rc, err = run(repo, capsys=capsys)
    assert rc == 1 and "scripts/jev_client.py was modified" in err and "no longer ships it" in err
    assert (repo / "scripts/jev_client.py").read_bytes() == b"edited\n"
    rc, err = run(repo, "--overwrite", capsys=capsys)
    assert rc == 0, err
    [backup] = [p for p in (repo / ".backup").iterdir() if p.name != ".gitignore"]
    assert (backup / "scripts/jev_client.py").read_bytes() == b"edited\n"
    assert not (repo / "scripts/jev_client.py").exists() and (repo / "scripts/other.sh").is_file()
    assert "scripts/jev_client.py" not in lock(repo)["files"]



@needs_sc_compose
def test_upgrade_from_0_6_removes_the_legacy_sanity_teammates_and_role_sheet(tmp_path, pkg_copy, capsys):
    # 0.6.x shipped dev-sanity-llm/-jev teammates and roles/dev-sanity.md; 0.7.0 folds them into agents/dev-sanity.md
    legacy = ["agents/dev-sanity-llm.md", "agents/dev-sanity-jev.md", "skills/atm-bd-orchestration/roles/dev-sanity.md"]
    for rel in legacy:
        (pkg_copy / rel).write_text(f"0.6.x {rel}\n")
    regenerate(pkg_copy)
    repo = make_repo(tmp_path)
    assert run(repo, *QA, pkg=pkg_copy)[0] == 0
    assert all((repo / ".claude" / rel).is_file() for rel in legacy)
    rc, err = run(repo, capsys=capsys)
    assert rc == 0, err
    assert not any((repo / ".claude" / rel).exists() for rel in legacy)
    assert not any(f".claude/{rel}" in lock(repo)["files"] for rel in legacy)
    assert (repo / ".claude/agents/dev-sanity.md").is_file()

# ---------------------------------------------------------------- migration from 0.x (no lock file)


@needs_sc_compose
def test_migration_owns_shipped_and_legacy_bytes_and_fails_on_others(tmp_path, pkg_copy, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    (repo / install.LOCK_OUT).unlink()          # a 0.x install had no record
    old_script = b"#!/usr/bin/env python3\n# as 0.2.x shipped it\n"
    script = "skills/atm-beads/scripts/resolve-role"
    (repo / ".claude" / script).write_bytes(old_script)
    dropped = "skills/atm-beads/scripts/export-sprint-index"
    (repo / ".claude" / dropped).write_bytes(b"old dropped script\n")
    legacy = json.loads((pkg_copy / install.LEGACY_OWNED).read_text())
    legacy["files"][script] = [sha(old_script)]
    legacy["files"][dropped] = [sha(b"old dropped script\n")]
    (pkg_copy / install.LEGACY_OWNED).write_text(json.dumps(legacy))
    # a file whose bytes no version shipped: not owned
    (repo / ".claude/skills/atm-beads/SKILL.md").write_text("hand edited\n")
    rc, err = run(repo, pkg=pkg_copy, capsys=capsys)
    assert rc == 1
    assert ".claude/skills/atm-beads/SKILL.md exists and is not owned by atm-bd-orchestration" in err
    assert script not in err and not (repo / install.LOCK_OUT).exists()
    (repo / ".claude/skills/atm-beads/SKILL.md").unlink()
    rc, err = run(repo, pkg=pkg_copy, capsys=capsys)
    assert rc == 0, err
    assert (repo / ".claude" / script).read_bytes() == (PKG / script).read_bytes()
    assert not (repo / ".claude" / dropped).exists()
    assert lock(repo)["version"] == "0.14.0"


def _git_has(rev: str) -> bool:
    return subprocess.run(["git", "-C", str(PKG), "cat-file", "-e", f"{rev}^{{commit}}"],
                          stderr=subprocess.DEVNULL).returncode == 0


@needs_sc_compose
@pytest.mark.skipif(not _git_has("d5b7301"), reason="package history (d5b7301) not in this checkout; fetch-depth 0 needed")
def test_real_upgrade_from_a_0_2_3_install(tmp_path, capsys):
    """Install with the 0.2.3 installer itself, then upgrade with this one."""
    top = subprocess.run(["git", "-C", str(PKG), "rev-parse", "--show-toplevel"], check=True,
                         stdout=subprocess.PIPE, text=True).stdout.strip()
    archive = subprocess.run(["git", "-C", top, "archive", "d5b7301:plugins/atm-bd-orchestration"],
                             check=True, stdout=subprocess.PIPE).stdout
    tarfile.open(fileobj=io.BytesIO(archive)).extractall(
        tmp_path / "old", **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    old_pkg = tmp_path / "old"
    repo = make_repo(tmp_path)
    (repo / ".atm.toml").write_text('[atm]\ndefault_team = "my-team"\n')
    old = subprocess.run(["python3", str(old_pkg / "install.py"), "--dest", str(repo / ".claude")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert old.returncode == 0, old.stdout
    assert not (repo / install.LOCK_OUT).exists()
    # Copied files from 0.2.3 are owned through config/legacy-owned.json. A rendered file
    # is owned only when it equals what this version renders, so a rendered file whose
    # source changed since 0.2.3 is refused by name; deleting it lets the upgrade through.
    # The set is what 0.2.3 rendered, which includes files this version only copies.
    # A rendered file this version no longer ships (roles/dev-sanity.md) is not installed, so not refused.
    renders = set(install.load_registry(old_pkg)["render"])
    changed = sorted(f".claude/{rel}" for rel in renders
                     if (old_pkg / rel).is_file() and (PKG / rel).is_file()
                     and (old_pkg / rel).read_bytes() != (PKG / rel).read_bytes())
    rc, err = run(repo, *QA, capsys=capsys)
    if changed:
        assert rc == 1
        refused = sorted(line.split(" exists and is not owned")[0] for line in err.split("; ") if "is not owned" in line)
        assert sorted(r.replace("install.py: ", "") for r in refused) == changed
        for key in changed:
            (repo / key).unlink()
        rc, err = run(repo, *QA, capsys=capsys)
    assert rc == 0, err
    files = lock(repo)["files"]
    assert ".claude/skills/atm-beads/scripts/validate-plan" in files and "scripts/jev_client.py" not in files
    assert ".claude/skills/atm-bd-orchestration/scripts/jev_client.py" in files and not (repo / "scripts/jev_client.py").exists()
    assert all(sha((repo / k).read_bytes()) == v for k, v in files.items())


# ---------------------------------------------------------------- targets and the sc-install hook


@needs_sc_compose
def test_codex_target_installs_skills_only_and_keeps_the_claude_record(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert run(repo, *QA)[0] == 0
    claude_keys = {k for k in lock(repo)["files"] if k.startswith(".claude/")}
    rc, err = run(repo, target=".codex", capsys=capsys)
    assert rc == 0, err
    assert not (repo / ".codex/agents").exists() and (repo / ".codex/skills/atm-beads/SKILL.md").is_file()
    files = lock(repo)["files"]
    assert claude_keys <= set(files) and ".codex/skills/atm-beads/SKILL.md" in files


@needs_sc_compose
def test_sc_install_hook_flow_matches_standalone(tmp_path):
    """prepare() -> sc-install's raw copy (skip existing) -> complete() -> cleanup()."""
    hooked = make_repo(tmp_path / "a")
    dest = hooked / ".claude"
    options = {"global": False, "local": True, "user": False, "project": False,
               "codex": False, "force": False, "expand": True, "args": {}}
    failed = install.prepare(str(PKG), str(dest), options)
    assert failed["result"] == "fail" and "qa_member" in failed["message"]
    options["args"] = {"qa_member": "quality-mgr"}
    assert install.prepare(str(PKG), str(dest), options) == {"result": "success"}
    assert not (hooked / install.LOCK_OUT).exists()   # prepare writes nothing
    artifacts = install.load_manifest_artifacts(PKG)
    for rel in artifacts["skills"] + artifacts["agents"]:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (dest / rel).exists():
            shutil.copy2(PKG / rel, dest / rel)
    assert install.complete(str(PKG), str(dest), options) == {"result": "success"}
    assert install.cleanup(str(PKG), str(dest), options) == {"result": "success"}
    standalone = make_repo(tmp_path / "b")
    assert run(standalone, *QA)[0] == 0
    a, b = snapshot(hooked), snapshot(standalone)
    norm = lambda snap, root: {k: v.replace(str(root.resolve()).encode(), b"ROOT").replace(str(root.resolve().parent).encode(), b"PARENT")
                               for k, v in snap.items() if k != install.LOCK_OUT}
    assert norm(a, hooked) == norm(b, standalone)
