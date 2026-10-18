from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from atm_bd_console import cli, skill, skill_source

COMMIT = "0123456789abcdef0123456789abcdef01234567"
PLUGIN = Path(__file__).resolve().parents[3] / "plugins" / "atm-bd-orchestration"


@dataclass
class FakePlan:
    writes: dict
    removals: list
    backups: list
    problems: list


class FakeInstaller:
    InstallError = RuntimeError

    def __init__(self):
        self.calls = []
        self.plan = FakePlan({".claude/one": (b"data", False)}, [], [], [])

    def check_tools(self):
        self.calls.append(("check_tools",))

    def parse_set(self, values):
        self.calls.append(("parse_set", tuple(values)))
        return {"bead_prefix": "atm"} if values else {}

    def plan_install(self, pkg_dir, dest, overrides, *, codex, overwrite):
        self.calls.append(("plan_install", pkg_dir, dest, overrides, codex, overwrite))
        return self.plan, {"unused": True}

    def package_version(self, pkg_dir):
        self.calls.append(("package_version", pkg_dir))
        return "0.16.0"

    def apply_plan(self, plan, version):
        self.calls.append(("apply_plan", plan, version))
        return 1


def source_context(source: skill_source.SkillSource):
    @contextmanager
    def resolve(**kwargs):
        yield source
    return resolve


def load_bundled_installer():
    spec = importlib.util.spec_from_file_location("_atm_bd_skill_integration_installer", PLUGIN / "install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.check_tools = lambda: None
    return module


def make_consumer(path: Path, installer, pkg_dir: Path = PLUGIN) -> Path:
    path.mkdir()
    (path / ".claude/agents").mkdir(parents=True)
    (path / ".beads").mkdir()
    (path / ".beads/metadata.json").write_text(json.dumps({"backend": "dolt", "dolt_mode": "server"}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", "https://github.com/example/repo.git"], check=True,
    )
    values = {}
    for name in installer.config_variables(pkg_dir):
        if name == "requirements_globs":
            values[name] = ["docs/requirements.md"]
        elif name == "adr_globs":
            values[name] = ["docs/adr/*.md"]
        elif name == "reviewers_round1":
            values[name] = ["req-qa", "arch-qa"]
        elif name not in installer.ROLE_KEYS:
            values[name] = f"test-{name}"
    values["roles"] = {"dev-sanity": "quality-mgr", "quality-mgr": "quality-mgr"}
    import yaml

    (path / ".claude/agents/registry.yaml").write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    for name in ("quality-mgr", "req-qa", "arch-qa"):
        (path / ".claude/agents" / f"{name}.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    legacy_path = "agents/dev-sanity-jev.md"
    assert legacy_path in installer.load_legacy_owned(pkg_dir)
    (path / ".claude" / legacy_path).write_bytes(b"legacy-owned fixture bytes\n")
    return path


def consumer_files(root: Path) -> dict[str, bytes]:
    files = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            key = path.relative_to(root).as_posix()
            files[re.sub(r"^\.backup/[^/]+/", ".backup/<run>/", key)] = path.read_bytes()
    return files


@pytest.fixture
def host_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    compose_version = importlib.metadata.version("sc-compose")
    outputs = {
        "python3": f'if [ "$1" = "--version" ]; then echo "Python 3.13.0"; else echo "{compose_version}"; fi',
        "sc-compose": f'echo "sc-compose {compose_version}"',
        "atm": 'echo "atm 99.0.0"',
        "atm-bd": 'echo "atm-bd 99.0.0"',
        "bd": 'echo "bd 99.0.0"',
        "jq": 'echo "jq-99.0.0"',
    }
    for name, body in outputs.items():
        executable = tool_dir / name
        executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool_dir}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.fixture
def skill_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    for name, value in (
        ("GIT_CONFIG_GLOBAL", os.devnull), ("GIT_CONFIG_NOSYSTEM", "1"),
        ("GIT_AUTHOR_NAME", "test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
        ("GIT_COMMITTER_NAME", "test"), ("GIT_COMMITTER_EMAIL", "test@example.invalid"),
    ):
        monkeypatch.setenv(name, value)

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(work))
    shutil.copytree(PLUGIN, work / "plugins" / "atm-bd-orchestration")
    git("-C", str(work), "add", ".")
    git("-C", str(work), "commit", "-q", "-m", "tagged release")
    tag_commit = git("-C", str(work), "rev-parse", "HEAD")
    git("-C", str(work), "tag", "v0.16.0")
    module = load_bundled_installer()
    (work / "plugins" / "atm-bd-orchestration" / module.CONFIG_TEMPLATE).unlink()
    git("-C", str(work), "add", "-A")
    git("-C", str(work), "commit", "-q", "-m", "unreadable main tree")
    main_commit = git("-C", str(work), "rev-parse", "HEAD")
    git("-C", str(work), "push", "-q", "origin", "main", "--tags")
    url = origin.as_uri()
    monkeypatch.setattr(skill_source, "REPOSITORY", url)
    return url, tag_commit, main_commit


def test_real_git_lists_and_clones_tag_and_main(skill_remote: tuple[str, str, str], capsys) -> None:
    _url, tag_commit, main_commit = skill_remote
    assert skill.run(["--list"]) == 0
    assert capsys.readouterr().out == "v0.16.0\norchestration\n"
    with skill_source.resolve_source(version="0.16.0") as source:
        assert (source.name, source.commit) == ("v0.16.0", tag_commit)
        assert (source.pkg_dir / "install.py").is_file()
        assert (source.pkg_dir / load_bundled_installer().CONFIG_TEMPLATE).is_file()
    assert not source.pkg_dir.exists()
    with skill_source.resolve_source(latest=True) as source:
        assert (source.name, source.commit) == ("main", main_commit)
        assert (source.pkg_dir / "install.py").is_file()
        assert not (source.pkg_dir / load_bundled_installer().CONFIG_TEMPLATE).exists()
    assert not source.pkg_dir.exists()


@pytest.mark.usefixtures("host_tools")
@pytest.mark.parametrize("failure", ["unknown", "git", "unreadable"])
def test_real_source_failures_leave_target_unchanged(
    failure: str, skill_remote: tuple[str, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    url, _tag_commit, _main_commit = skill_remote
    target = tmp_path / "target"
    target.mkdir()
    (target / "sentinel").write_bytes(b"do not change")
    before = consumer_files(target)
    if failure == "unknown":
        args = ["9.9.9"]
        expected = "unknown version 9.9.9; available tags: v0.16.0"
    elif failure == "git":
        url = (tmp_path / "missing.git").as_uri()
        monkeypatch.setattr(skill_source, "REPOSITORY", url)
        args = ["--latest"]
        expected = f"{url}: git ls-remote --tags --refs {url} failed:"
    else:
        args = ["--latest"]
        expected = f"main is not installable by atm-bd {skill.__version__}:"

    assert skill.run([*args, "--dest", str(target)]) == 1
    error = capsys.readouterr().err
    assert expected in error
    if failure == "git":
        assert "fatal:" in error
    if failure == "unreadable":
        assert load_bundled_installer().CONFIG_TEMPLATE in error
    assert consumer_files(target) == before
    assert sorted(path.relative_to(target).as_posix() for path in target.rglob("*")) == ["sentinel"]


@pytest.fixture
def installer(monkeypatch, tmp_path: Path):
    fake = FakeInstaller()
    monkeypatch.setattr(skill, "_installer", lambda: fake)
    pkg = tmp_path / "package-data"
    pkg.mkdir()
    source = skill_source.SkillSource("bundled", pkg, COMMIT)
    monkeypatch.setattr(skill.skill_source, "resolve_source", source_context(source))
    return fake, source


def test_bundled_default_calls_installer_functions_in_the_app_interpreter(installer, tmp_path: Path, capsys) -> None:
    fake, source = installer
    target = tmp_path / "consumer"
    target.mkdir()

    assert skill.run(["--dest", str(target)]) == 0

    assert [call[0] for call in fake.calls] == ["check_tools", "parse_set", "plan_install", "package_version", "apply_plan"]
    plan_call = next(call for call in fake.calls if call[0] == "plan_install")
    assert plan_call[1] == source.pkg_dir
    assert plan_call[2] == target / ".claude"
    assert plan_call[3:] == ({}, False, False)
    output = capsys.readouterr().out
    assert output.endswith(f"installed atm-bd-orchestration orchestration from bundled @ {COMMIT} (atm-bd 0.17.0)\n")


def test_bundled_default_uses_current_directory(installer, tmp_path: Path, monkeypatch) -> None:
    fake, _ = installer
    monkeypatch.setattr(skill.Path, "cwd", classmethod(lambda cls: tmp_path))

    assert skill.run([]) == 0

    plan_call = next(call for call in fake.calls if call[0] == "plan_install")
    assert plan_call[2] == tmp_path / ".claude"


@pytest.mark.parametrize("commit", [COMMIT, "unknown"])
def test_source_line_prints_the_commit_the_source_resolved(commit: str) -> None:
    # skill_source owns the 40-hex-or-unknown rule; the printed line echoes that value.
    assert skill._source_line("main", commit) == (
        f"installed atm-bd-orchestration orchestration from main @ {commit} (atm-bd {skill.__version__})"
    )


def test_orchestration_flag_and_installer_passthrough(installer, tmp_path: Path) -> None:
    fake, _ = installer
    target = tmp_path / "consumer"
    target.mkdir()

    assert skill.run(["--orchestration", "--codex", "--overwrite", "--set", "bead_prefix=atm", "--dest", str(target)]) == 0

    plan_call = next(call for call in fake.calls if call[0] == "plan_install")
    assert plan_call[2] == target / ".codex"
    assert plan_call[3:] == ({"bead_prefix": "atm"}, True, True)


def test_list_prints_remote_tags_and_set_without_loading_or_running_installer(monkeypatch, capsys) -> None:
    monkeypatch.setattr(skill.skill_source, "list_tags", lambda: ("v0.16.0", "v0.16.1"))
    monkeypatch.setattr(skill, "_installer", lambda: (_ for _ in ()).throw(AssertionError("installer")))

    assert skill.run(["--list"]) == 0
    assert capsys.readouterr().out == "v0.16.0\nv0.16.1\norchestration\n"


@pytest.mark.parametrize("args", [["--list", "--codex"], ["--list", "--dest", "ignored"]])
def test_list_rejects_install_options(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        skill.run(args)
    assert error.value.code == 2


def test_cli_passes_skill_arguments_to_skill_parser(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["skill", "--list", "--dest", str(tmp_path)])
    assert error.value.code == 2
    assert "--list cannot be combined" in capsys.readouterr().err


def test_remote_tree_installer_failure_is_reported_before_apply(installer, tmp_path: Path, monkeypatch, capsys) -> None:
    fake, _ = installer
    plugin = tmp_path / "remote-package"
    plugin.mkdir()
    source = skill_source.SkillSource("0.16.0", plugin, COMMIT)
    monkeypatch.setattr(skill.skill_source, "resolve_source", source_context(source))
    fake.plan_install = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad manifest"))

    assert skill.run(["0.16.0", "--dest", str(tmp_path / "consumer")]) == 1
    assert not any(call[0] == "apply_plan" for call in fake.calls)
    error = capsys.readouterr().err
    assert "0.16.0 is not installable by atm-bd 0.17.0: bad manifest" in error


def _raise(error: Exception):
    def fail(*_args, **_kwargs):
        raise error

    return fail


@pytest.mark.parametrize(
    ("step", "error", "message"),
    [
        ("check_tools", RuntimeError("sc-compose CLI not found"), "sc-compose CLI not found"),
        ("apply_plan", OSError("target is not writable"), "target is not writable"),
    ],
    ids=["prerequisite", "target-write"],
)
def test_tag_install_does_not_blame_the_ref_for_host_or_target_failures(
    installer, tmp_path: Path, monkeypatch, capsys, step: str, error: Exception, message: str,
) -> None:
    fake, _ = installer
    remote = tmp_path / "remote-package"
    remote.mkdir()
    source = skill_source.SkillSource("v0.16.0", remote, COMMIT)
    monkeypatch.setattr(skill.skill_source, "resolve_source", source_context(source))
    setattr(fake, step, _raise(error))

    assert skill.run(["0.16.0", "--dest", str(tmp_path / "consumer")]) == 1
    assert capsys.readouterr().err == f"atm-bd skill: {message}\n"


def test_fetched_installer_file_is_never_loaded_or_executed(installer, tmp_path: Path, monkeypatch) -> None:
    fake, _ = installer
    marker = tmp_path / "executed"
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "install.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n", encoding="utf-8")
    source = skill_source.SkillSource("main", remote, COMMIT)
    monkeypatch.setattr(skill.skill_source, "resolve_source", source_context(source))

    assert skill.run(["--latest", "--dest", str(tmp_path / "consumer")]) == 0
    assert not marker.exists()
    assert any(call[0] == "plan_install" and call[1] == remote for call in fake.calls)


@pytest.mark.parametrize(("flags", "subdir", "conflict"), [
    ((), ".claude", False),
    (("--codex",), ".codex", False),
    (("--overwrite",), ".claude", True),
], ids=["default", "codex", "overwrite"])
def test_bundled_skill_matches_install_py_file_for_file_offline_with_legacy_owned_file(
    tmp_path: Path, monkeypatch, host_tools, flags: tuple[str, ...], subdir: str, conflict: bool,
) -> None:
    module = load_bundled_installer()
    pkg_dir = tmp_path / "plugin"
    shutil.copytree(PLUGIN, pkg_dir)
    legacy_file = pkg_dir / "config/legacy-owned.json"
    legacy = json.loads(legacy_file.read_text(encoding="utf-8"))
    legacy["files"]["agents/dev-sanity-jev.md"] = [module.sha256(b"legacy-owned fixture bytes\n")]
    legacy_file.write_text(json.dumps(legacy), encoding="utf-8")
    direct = make_consumer(tmp_path / "direct", module, pkg_dir)
    through_skill = make_consumer(tmp_path / "through-skill", module, pkg_dir)
    if conflict:  # a modified shipped file: refused without --overwrite, backed up with it
        for consumer in (direct, through_skill):
            shipped = consumer / ".claude/agents/ceremony-qa.md"
            shipped.parent.mkdir(parents=True, exist_ok=True)
            shipped.write_bytes(b"locally modified\n")
    monkeypatch.setattr(skill.skill_source, "bundled_directory", lambda: pkg_dir)
    monkeypatch.setattr(skill.skill_source, "_run_git", lambda *_: (_ for _ in ()).throw(AssertionError("network")))

    assert module.main(["--dest", str(direct / subdir), *flags], pkg_dir=pkg_dir) == 0
    assert skill.run(["--dest", str(through_skill), *flags]) == 0

    assert consumer_files(through_skill) == consumer_files(direct)


def test_skill_is_wired_into_cli_and_help(capsys) -> None:
    with pytest.raises(SystemExit) as top:
        cli.main(["--help"])
    assert top.value.code == 0
    assert "skill" not in capsys.readouterr().out

    with pytest.raises(SystemExit) as sub:
        cli.main(["skill", "--help"])
    assert sub.value.code == 0
    output = capsys.readouterr().out
    assert "--orchestration" in output and "--latest" in output and "--list" in output
