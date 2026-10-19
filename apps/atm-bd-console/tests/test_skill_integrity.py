"""The published skill manifest: built into the wheel and sdist, verified by `atm-bd skill`, recorded at install.

Every source is a fixture tree with controlled bytes; git and GitHub are injected, so nothing here touches the network.
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
import sc_sha
from process_helpers import build_command, run_process

from atm_bd_console import cli, skill, skill_integrity, skill_source

COMMIT = "0123456789abcdef0123456789abcdef01234567"
APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
UV = shutil.which("uv")
VERSION = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def sha(data: bytes) -> str:
    return sc_sha.calculate_hash({"utf8_file_bytes": data})["sha256"]


def make_tree(root: Path) -> Path:
    files = {
        "install.py": "# installer data\n",
        "manifest.yaml": "version: 0.17.4\n",
        "registry.yaml": "{}\n",
        "skills/demo/SKILL.md": "# demo\n",
        "skills/demo/scripts/tool.py": "print('tool')\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    cache = root / "skills/demo/scripts/__pycache__/tool.cpython-311.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\x00byte-compiled by the installer")
    (root / "skills/demo/stray.pyc").write_bytes(b"\x00compiled")
    return root


def manifest_bytes(root: Path, version: str = "0.17.4") -> bytes:
    return json.dumps(skill_integrity.build_manifest(root, version)).encode()


def sdist(version: str, manifest: bytes | None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        members = {f"atm_bd-{version}/PKG-INFO": b"Metadata-Version: 2.4\n"}
        if manifest is not None:
            members[f"atm_bd-{version}/src/atm_bd_console/skill-manifest.json"] = manifest
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# --- the manifest -------------------------------------------------------------------------------------------------


def test_manifest_lists_every_shipped_file_sorted_with_its_sc_sha_and_the_composition_aggregate(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "plugin")
    manifest = skill_integrity.build_manifest(root, "0.17.4")

    assert manifest["schema"] == 1 and manifest["version"] == "0.17.4"
    assert [entry["path"] for entry in manifest["files"]] == [
        "install.py", "manifest.yaml", "registry.yaml", "skills/demo/SKILL.md", "skills/demo/scripts/tool.py",
    ]
    assert all(entry["sc_sha"] == sha((root / entry["path"]).read_bytes()) for entry in manifest["files"])
    nodes = [{"source": {"kind": "local_path", "value": e["path"]}, "sha256": e["sc_sha"]} for e in manifest["files"]]
    expected = sc_sha.calculate_composition_hash({"schema": "sc-sha/manifest/v1", "nodes": nodes, "edges": []})
    assert manifest["aggregate"] == expected["sha256"]
    assert skill_integrity.parse_manifest(json.dumps(manifest).encode(), "ref") == manifest


def test_changing_one_file_changes_only_its_entry_and_the_aggregate(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "plugin")
    before = skill_integrity.build_manifest(root, "0.17.4")
    (root / "skills/demo/SKILL.md").write_text("# demo, changed\n", encoding="utf-8")
    after = skill_integrity.build_manifest(root, "0.17.4")

    changed = [b["path"] for b, a in zip(before["files"], after["files"], strict=True) if b != a]
    assert changed == ["skills/demo/SKILL.md"]
    assert before["aggregate"] != after["aggregate"]


@pytest.mark.parametrize("mutate", ["aggregate", "unsorted", "schema", "not-json", "entry"])
def test_a_malformed_published_manifest_is_refused(tmp_path: Path, mutate: str) -> None:
    manifest = skill_integrity.build_manifest(make_tree(tmp_path / "plugin"), "0.17.4")
    if mutate == "aggregate":
        manifest["files"][0]["sc_sha"] = "0" * 64
    elif mutate == "unsorted":
        manifest["files"].reverse()
    elif mutate == "schema":
        manifest["schema"] = 2
    elif mutate == "entry":
        manifest["files"][0] = {"path": "install.py"}
    data = b"{" if mutate == "not-json" else json.dumps(manifest).encode()
    with pytest.raises(skill_integrity.IntegrityError, match=r"^v0\.17\.4: skill-manifest\.json"):
        skill_integrity.parse_manifest(data, "v0.17.4")


def test_verify_tree_reports_ok_modified_missing_and_extra_paths(tmp_path: Path) -> None:
    root = make_tree(tmp_path / "plugin")
    manifest = skill_integrity.build_manifest(root, "0.17.4")
    assert {r.status for r in skill_integrity.verify_tree(root, manifest)} == {"ok"}

    (root / "skills/demo/SKILL.md").write_text("tampered\n", encoding="utf-8")
    (root / "registry.yaml").unlink()
    (root / "skills/demo/planted.md").write_text("not published\n", encoding="utf-8")

    assert [(r.status, r.path) for r in skill_integrity.verify_tree(root, manifest) if r.status != "ok"] == [
        ("missing", "registry.yaml"), ("modified", "skills/demo/SKILL.md"), ("extra", "skills/demo/planted.md"),
    ]


def test_load_manifest_without_published_bytes_names_the_ref() -> None:
    source = skill_source.SkillSource("v0.17.3", Path("unused"), COMMIT)
    with pytest.raises(skill_integrity.IntegrityError, match=r"^v0\.17\.3 has no published skill-manifest\.json; cannot verify$"):
        skill_integrity.load_manifest(source)


# --- atm-bd skill through the CLI -----------------------------------------------------------------------------------


@dataclass
class FakePlan:
    writes: dict
    removals: list = field(default_factory=list)
    backups: list = field(default_factory=list)
    problems: list = field(default_factory=list)


class FakeInstaller:
    """install.py's API; the plan renders a team value into one file, as the real installer does."""
    InstallError = RuntimeError

    def __init__(self) -> None:
        self.calls: list[str] = []

    def check_tools(self):
        self.calls.append("check_tools")

    def parse_set(self, values):
        return {}

    def plan_install(self, pkg_dir, dest, overrides, *, codex, overwrite):
        self.calls.append("plan_install")
        prefix = dest.name
        writes = {f"{prefix}/skills/demo/SKILL.md": ((pkg_dir / "skills/demo/SKILL.md").read_bytes() + b"team: atm-dev\n", False),
                  f"{prefix}/skills/demo/scripts/tool.py": ((pkg_dir / "skills/demo/scripts/tool.py").read_bytes(), True)}
        return FakePlan(writes), {}

    def package_version(self, pkg_dir):
        return "0.16.0"

    def apply_plan(self, plan, version):
        self.calls.append("apply_plan")
        root = Path(self.dest_root)
        for key, (data, _executable) in plan.writes.items():
            (root / key).parent.mkdir(parents=True, exist_ok=True)
            (root / key).write_bytes(data)
        return len(plan.writes)


class Upstream:
    """Injected git and GitHub: tags, the tree each tag clones to, and each release's published assets."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.trees: dict[str, Path] = {}
        self.assets: dict[str, bytes] = {}
        self.latest: str | None = None
        self.clones: list[str] = []
        self.urls: list[str] = []
        monkeypatch.setattr(skill_source, "_run_git", self.git)
        monkeypatch.setattr(skill_source, "_http_get", self.http)

    def release(self, tag: str, *, published: bool = True, manifest_tree: Path | None = None) -> Path:
        tree = make_tree(self.tmp_path / f"release-{tag}")
        self.trees[tag] = tree
        version = tag.removeprefix("v")
        if published:
            self.assets[f"{skill_source.RELEASES}/{tag}/atm_bd-{version}.tar.gz"] = sdist(
                version, manifest_bytes(manifest_tree or tree, version))
        return tree

    def git(self, args, **_kwargs):
        if args[0] == "ls-remote":
            return "".join(f"{COMMIT}\trefs/tags/{tag}\n" for tag in self.trees)
        if args[0] == "clone":
            ref = args[args.index("--branch") + 1]
            self.clones.append(ref)
            shutil.copytree(self.trees[ref], Path(args[-1]) / "plugins" / "atm-bd-orchestration")
            return ""
        if args[0] == "-C":
            return COMMIT + "\n"
        raise AssertionError(args)

    def http(self, url: str) -> bytes | None:
        self.urls.append(url)
        if url == skill_source.LATEST_RELEASE_API:
            return None if self.latest is None else json.dumps({"tag_name": self.latest}).encode()
        return self.assets.get(url)


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeInstaller:
    fake = FakeInstaller()
    fake.dest_root = str(tmp_path / "consumer")
    (tmp_path / "consumer").mkdir()
    monkeypatch.setattr(skill, "_installer", lambda: fake)
    return fake


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Upstream:
    return Upstream(tmp_path, monkeypatch)


def consumer(tmp_path: Path) -> dict[str, bytes]:
    root = tmp_path / "consumer"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, published: bool = True) -> Path:
    """An installed wheel's layout: atm_bd_console/_skills beside atm_bd_console/skill-manifest.json."""
    package = tmp_path / "site-packages" / "atm_bd_console"
    tree = make_tree(package / "_skills")
    if published:
        (package / "skill-manifest.json").write_bytes(manifest_bytes(tree))
    monkeypatch.setattr(skill_source, "__file__", str(package / "skill_source.py"))
    return tree


def run_cli(args: list[str], tmp_path: Path) -> int:
    return cli.main(["skill", *args, "--dest", str(tmp_path / "consumer")])


def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(skill_source, "_run_git", fail)
    monkeypatch.setattr(skill_source, "_http_get", fail)


def test_bare_install_verifies_the_packaged_manifest_with_the_network_disabled(
    installer, tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle(tmp_path, monkeypatch)
    no_network(monkeypatch)

    assert run_cli([], tmp_path) == 0

    assert installer.calls == ["check_tools", "plan_install", "apply_plan"]
    out = capsys.readouterr().out
    assert out.endswith(f"installed atm-bd-orchestration orchestration from bundled @ unknown (atm-bd {skill.__version__})\n")


@pytest.mark.parametrize("args", [["0.17.3"], ["--latest"]], ids=["version", "latest"])
def test_release_install_verifies_the_tag_against_its_release_sdist_manifest(
    installer, upstream: Upstream, tmp_path: Path, args: list[str], capsys,
) -> None:
    upstream.release("v0.17.3")
    upstream.latest = "v0.17.3"

    assert run_cli(args, tmp_path) == 0

    assert upstream.clones == ["v0.17.3"]
    assert f"{skill_source.RELEASES}/v0.17.3/atm_bd-0.17.3.tar.gz" in upstream.urls
    assert installer.calls[-1] == "apply_plan"
    assert capsys.readouterr().out.endswith(
        f"installed atm-bd-orchestration orchestration from v0.17.3 @ {COMMIT} (atm-bd {skill.__version__})\n")


def test_latest_installs_the_latest_published_release_not_main(installer, upstream: Upstream, tmp_path: Path) -> None:
    upstream.release("v0.17.2")
    upstream.release("v0.17.4")
    upstream.latest = "v0.17.4"

    assert run_cli(["--latest"], tmp_path) == 0

    assert upstream.clones == ["v0.17.4"]
    assert upstream.urls[0] == skill_source.LATEST_RELEASE_API


def _plant(tree: Path, change: str) -> str:
    if change == "modified":
        (tree / "skills/demo/SKILL.md").write_text("# tampered\n", encoding="utf-8")
        return "modified skills/demo/SKILL.md"
    if change == "missing":
        (tree / "skills/demo/scripts/tool.py").unlink()
        return "missing skills/demo/scripts/tool.py"
    (tree / "skills/demo/planted.py").write_text("print('planted')\n", encoding="utf-8")
    return "extra skills/demo/planted.py"


@pytest.mark.parametrize("change", ["modified", "missing", "extra"])
@pytest.mark.parametrize("args", [[], ["0.17.3"], ["--latest"]], ids=["bare", "version", "latest"])
def test_a_tree_that_differs_from_its_manifest_is_refused_before_any_write(
    installer, upstream: Upstream, tmp_path: Path, monkeypatch, capsys, args: list[str], change: str,
) -> None:
    if args:
        published = make_tree(tmp_path / "published")
        tree = upstream.release("v0.17.3", manifest_tree=published)
        upstream.latest = "v0.17.3"
    else:
        tree = bundle(tmp_path, monkeypatch)
    line = _plant(tree, change)
    before = consumer(tmp_path)

    assert run_cli(args, tmp_path) == 1

    err = capsys.readouterr().err.splitlines()
    assert err[0] == line
    ref = "bundled" if not args else "v0.17.3"
    assert err[1] == f"atm-bd skill: {ref} differs from its published skill-manifest.json in 1 file(s); nothing installed"
    assert "plan_install" not in installer.calls and "apply_plan" not in installer.calls
    assert consumer(tmp_path) == before == {}


@pytest.mark.parametrize("asset", ["no-sdist", "sdist-without-manifest"])
@pytest.mark.parametrize("args", [["0.17.3"], ["--latest"]], ids=["version", "latest"])
def test_a_release_without_a_published_manifest_is_refused_before_any_write(
    installer, upstream: Upstream, tmp_path: Path, capsys, args: list[str], asset: str,
) -> None:
    upstream.release("v0.17.3", published=False)
    upstream.latest = "v0.17.3"
    if asset == "sdist-without-manifest":
        upstream.assets[f"{skill_source.RELEASES}/v0.17.3/atm_bd-0.17.3.tar.gz"] = sdist("0.17.3", None)

    assert run_cli(args, tmp_path) == 1

    assert capsys.readouterr().err == "atm-bd skill: v0.17.3 has no published skill-manifest.json; cannot verify\n"
    assert upstream.clones == [] and installer.calls == ["check_tools"]
    assert consumer(tmp_path) == {}


def test_latest_with_no_published_release_is_refused(installer, upstream: Upstream, tmp_path: Path, capsys) -> None:
    upstream.release("v0.17.3")

    assert run_cli(["--latest"], tmp_path) == 1
    assert capsys.readouterr().err == "atm-bd skill: latest release has no published skill-manifest.json; cannot verify\n"
    assert consumer(tmp_path) == {}


def test_bundle_without_a_packaged_manifest_is_refused(installer, tmp_path: Path, monkeypatch, capsys) -> None:
    bundle(tmp_path, monkeypatch, published=False)
    no_network(monkeypatch)

    assert run_cli([], tmp_path) == 1
    assert capsys.readouterr().err == "atm-bd skill: bundled has no published skill-manifest.json; cannot verify\n"
    assert consumer(tmp_path) == {}


def test_install_record_lists_version_commit_source_and_the_sc_sha_of_each_installed_file(
    installer, upstream: Upstream, tmp_path: Path,
) -> None:
    tree = upstream.release("v0.17.3")

    assert run_cli(["0.17.3"], tmp_path) == 0

    installed = consumer(tmp_path)
    record = json.loads(installed.pop(".claude/atm-bd-install.json"))
    published = json.loads(manifest_bytes(tree, "0.17.3"))
    assert record == {
        "version": "0.17.3",
        "commit": COMMIT,
        "source": {"ref": "v0.17.3", "manifest_aggregate": published["aggregate"]},
        "files": [{"path": path, "sc_sha": sha(data)} for path, data in sorted(installed.items())],
    }
    # The rendered file is recorded as installed, not as published.
    rendered = next(e for e in record["files"] if e["path"] == ".claude/skills/demo/SKILL.md")
    assert rendered["sc_sha"] != next(e["sc_sha"] for e in published["files"] if e["path"] == "skills/demo/SKILL.md")


# --- the built artifacts --------------------------------------------------------------------------------------------


def _archive_manifest(archive: Path) -> dict:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            return json.loads(wheel.read("atm_bd_console/skill-manifest.json"))
    with tarfile.open(archive) as source:
        member, = (m for m in source.getmembers() if m.name.endswith("/src/atm_bd_console/skill-manifest.json")
                   and m.name.count("/") == 3)
        return json.loads(source.extractfile(member).read())


def test_build_hook_includes_a_manifest_for_a_fixture_tree(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("atm_bd_hatch_build", APP / "hatch_build.py")
    assert spec is not None and spec.loader is not None
    hatch_build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hatch_build)

    source = make_tree(tmp_path / "plugin")
    hook = SimpleNamespace(target_name="wheel", _manifest_dir=None, _generated_identity=False)
    hook._dest = lambda name: f"atm_bd_console/{name}"
    hook._cleanup_manifest = MethodType(hatch_build.CustomBuildHook._cleanup_manifest, hook)
    build_data = {"force_include": {}}

    hatch_build.CustomBuildHook._include_manifest(hook, APP, source, build_data, "0.17.4")

    generated, destination = next(iter(build_data["force_include"].items()))
    assert destination == "atm_bd_console/skill-manifest.json"
    manifest_path = Path(generated)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == skill_integrity.build_manifest(source, "0.17.4")
    hatch_build.CustomBuildHook.finalize(hook, "0.17.4", build_data, "fixture.whl")
    assert not manifest_path.exists()


def test_build_hook_cleans_manifest_temp_dir_when_initialization_fails(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("atm_bd_hatch_build_cleanup", APP / "hatch_build.py")
    assert spec is not None and spec.loader is not None
    hatch_build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hatch_build)
    hook = SimpleNamespace(_manifest_dir=None, _generated_identity=False)
    temporary = tmp_path / "manifest-temp"
    temporary.mkdir()
    (temporary / "skill-manifest.json").write_text("{}\n", encoding="utf-8")

    def fail_initialization(_version, _build_data):
        hook._manifest_dir = temporary
        raise RuntimeError("fixture build failure")

    hook._initialize = fail_initialization
    hook._cleanup_manifest = MethodType(hatch_build.CustomBuildHook._cleanup_manifest, hook)
    with pytest.raises(RuntimeError, match="fixture build failure"):
        hatch_build.CustomBuildHook.initialize(hook, "0.17.4", {"force_include": {}})

    assert not temporary.exists() and hook._manifest_dir is None


def _bundled(archive: Path) -> dict[str, bytes]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            prefix = "atm_bd_console/_skills/"
            return {n[len(prefix):]: wheel.read(n) for n in wheel.namelist() if n.startswith(prefix) and not n.endswith("/")}
    with tarfile.open(archive) as source:
        found = {}
        for member in source.getmembers():
            head, _, rest = member.name.partition("/src/atm_bd_console/_skills/")
            if member.isfile() and rest and "/" not in head:
                found[rest] = source.extractfile(member).read()
        return found


def test_built_wheel_and_sdist_publish_a_manifest_of_exactly_the_bundled_files(tmp_path: Path) -> None:
    if UV is None:
        pytest.fail("uv is required to build and verify the wheel and sdist; install uv in the package-test environment")
    checkout = tmp_path / "checkout"
    run_process("clone", ["git", "clone", "--no-hardlinks", str(REPO), str(checkout)], cwd=tmp_path)
    app = checkout / "apps" / "atm-bd-console"
    for name in ("hatch_build.py", "pyproject.toml", "uv.lock", "src/atm_bd_console/skill.py",
                 "src/atm_bd_console/skill_source.py", "src/atm_bd_console/skill_integrity.py"):
        shutil.copy2(APP / name, app / name)

    def build(out: str, *args: str) -> None:
        run_process("build", [*build_command(*args, "--out-dir", str(tmp_path / out))], cwd=app)

    build("sdist", "--sdist")
    build("wheel", "--wheel")
    sdist_path = next((tmp_path / "sdist").glob("*.tar.gz"))
    build("rebuilt", "--wheel", str(sdist_path))
    archives = [next((tmp_path / "wheel").glob("*.whl")), sdist_path, next((tmp_path / "rebuilt").glob("*.whl"))]
    for archive in archives:
        manifest = _archive_manifest(archive)
        bundled = _bundled(archive)
        shipped = {p: d for p, d in bundled.items() if "__pycache__" not in p.split("/") and not p.endswith(".pyc")}
        assert [e["path"] for e in manifest["files"]] == sorted(shipped)
        assert all(e["sc_sha"] == sha(shipped[e["path"]]) for e in manifest["files"])
        assert manifest["aggregate"] == skill_integrity.aggregate(manifest["files"])
        assert manifest["schema"] == 1 and manifest["version"] == VERSION
    first = _archive_manifest(archives[0])
    assert _archive_manifest(archives[1]) == first == _archive_manifest(archives[2])

    changed = checkout / "plugins/atm-bd-orchestration/README.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    build("changed", "--wheel")
    after = _archive_manifest(next((tmp_path / "changed").glob("*.whl")))
    differing = [b["path"] for b, a in zip(first["files"], after["files"], strict=True) if b != a]
    assert differing == ["README.md"]
    assert after["aggregate"] != first["aggregate"]
