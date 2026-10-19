from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

import pytest

from atm_bd_console import __version__, command, skill_source
from atm_bd_console.result import Err, Ok, Timeout

COMMIT = "0123456789abcdef0123456789abcdef01234567"
# resolve_source only carries the published bytes; parsing and verification are skill_integrity's (test_skill_integrity).
PUBLISHED = b'{"published": true}'


@pytest.fixture(autouse=True)
def published(monkeypatch):
    """Every release and the bundled tree publish a manifest unless a test removes it."""
    fetched: list[str] = []
    monkeypatch.setattr(skill_source, "bundled_manifest", lambda: PUBLISHED)
    monkeypatch.setattr(skill_source, "release_manifest", lambda tag: fetched.append(tag) or PUBLISHED)
    monkeypatch.setattr(skill_source, "latest_release_tag", lambda: "v0.16.0")
    return fetched


def make_plugin(path: Path, *, installer: str = "# installer data\n") -> Path:
    plugin = path / "plugins" / "atm-bd-orchestration"
    plugin.mkdir(parents=True)
    (plugin / "install.py").write_text(installer, encoding="utf-8")
    (plugin / "manifest.yaml").write_text("version: 0.16.0\n", encoding="utf-8")
    (plugin / "registry.yaml").write_text("{}\n", encoding="utf-8")
    return plugin


def test_remote_tags_are_sorted_like_git_tag_list(monkeypatch) -> None:
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        return (
            "b\trefs/tags/v0.9.0\n"
            "a\trefs/tags/v0.16.0\n"
            "c\trefs/tags/v0.10.0\n"
            "ignored\trefs/heads/main\n"
        )

    monkeypatch.setattr(skill_source, "_run_git", run)

    assert skill_source.remote_tags() == ("v0.10.0", "v0.16.0", "v0.9.0")
    assert calls == [["ls-remote", "--tags", "--refs", skill_source.REPOSITORY]]


def test_bundled_source_needs_no_network_and_uses_packaged_data(monkeypatch, tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(skill_source, "bundled_directory", lambda: plugin)
    monkeypatch.setattr(skill_source, "_run_git", lambda *_: (_ for _ in ()).throw(AssertionError("network/git")))

    with skill_source.resolve_source() as source:
        assert source.name == "bundled"
        assert source.pkg_dir == plugin
        assert source.commit == "unknown"
        assert source.manifest == PUBLISHED


@pytest.mark.parametrize("commit", [COMMIT, "not-a-commit"], ids=["valid", "invalid"])
def test_build_commit_reads_and_validates_the_shared_identity_file(monkeypatch, tmp_path: Path, commit: str) -> None:
    module = tmp_path / "atm_bd_console" / "skill_source.py"
    module.parent.mkdir()
    (module.parent / "_build_identity.py").write_text(f"IDENTITY = '0.17.4'\nCOMMIT = {commit!r}\n", encoding="utf-8")
    monkeypatch.setattr(skill_source, "__file__", str(module))

    assert skill_source.build_commit() == (commit if commit == COMMIT else "unknown")


def test_bundled_directory_is_only_the_packaged_skills(monkeypatch, tmp_path: Path) -> None:
    # The removed fallback read the plugin four directories above the module; that tree must not be consulted.
    module = tmp_path / "a" / "b" / "c" / "d" / "skill_source.py"
    make_plugin(tmp_path)
    monkeypatch.setattr(skill_source, "__file__", str(module))

    with pytest.raises(skill_source.SkillSourceError, match="bundled skills are missing"):
        skill_source.bundled_directory()

    packaged = module.parent / "_skills"
    packaged.mkdir(parents=True)
    assert skill_source.bundled_directory() == packaged.resolve()


def test_release_resolves_to_shallow_clone_as_data_without_running_it(monkeypatch, tmp_path: Path) -> None:
    calls = []
    cloned = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[:3] == ["ls-remote", "--tags", "--refs"]:
            return f"{COMMIT}\trefs/tags/v0.16.0\n"
        if args[0] == "clone":
            checkout = Path(args[-1])
            plugin = make_plugin(checkout)
            marker = tmp_path / "fetched-code-ran"
            (plugin / "install.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n", encoding="utf-8")
            cloned.append(plugin)
            return ""
        if args[0] == "-C" and args[1].endswith("/checkout"):
            return COMMIT + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(skill_source, "_run_git", run)

    with skill_source.resolve_source(version="0.16.0") as source:
        assert source.name == "v0.16.0"
        assert source.pkg_dir == cloned[0]
        assert source.commit == COMMIT
        assert source.pkg_dir.joinpath("install.py").is_file()
        assert not (tmp_path / "fetched-code-ran").exists()
        assert calls[1][0:6] == ["clone", "--depth", "1", "--single-branch", "--branch", "v0.16.0"]

    assert not source.pkg_dir.exists()


def test_latest_clones_the_latest_release_tag_not_main(monkeypatch, published) -> None:
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[0] == "ls-remote":
            return f"{COMMIT}\trefs/tags/v0.16.0\n{COMMIT}\trefs/tags/v0.17.4\n"
        if args[0] == "clone":
            make_plugin(Path(args[-1]))
            return ""
        if args[0] == "-C":
            return COMMIT
        raise AssertionError(args)

    monkeypatch.setattr(skill_source, "_run_git", run)
    monkeypatch.setattr(skill_source, "latest_release_tag", lambda: "v0.17.4")

    with skill_source.resolve_source(latest=True) as source:
        assert source.name == "v0.17.4"
        assert source.commit == COMMIT
        assert source.manifest == PUBLISHED
    clone = next(args for args in calls if args[0] == "clone")
    assert clone[4:6] == ["--branch", "v0.17.4"] and "main" not in clone
    assert published == ["v0.17.4"]


def test_latest_without_a_published_release_is_refused_before_cloning(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(skill_source, "_run_git", lambda args, **_kwargs: calls.append(args) or f"{COMMIT}\trefs/tags/v0.16.0\n")
    monkeypatch.setattr(skill_source, "latest_release_tag", lambda: None)

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"^latest release has no published skill-manifest\.json; cannot verify$"),
        skill_source.resolve_source(latest=True),
    ):
        raise AssertionError("no release must not resolve")
    assert [args[0] for args in calls] == ["ls-remote"]


def test_latest_release_whose_tag_is_not_upstream_is_a_repository_error(monkeypatch) -> None:
    monkeypatch.setattr(skill_source, "_run_git", lambda args, **_kwargs: f"{COMMIT}\trefs/tags/v0.16.0\n")
    monkeypatch.setattr(skill_source, "latest_release_tag", lambda: "v0.17.4")

    with pytest.raises(skill_source.RepositoryError, match="latest release v0.17.4 has no tag"), skill_source.resolve_source(latest=True):
        raise AssertionError("an untagged release must not resolve")


@pytest.mark.parametrize("latest", [False, True], ids=["version", "latest"])
def test_release_without_a_published_manifest_is_refused_before_cloning(monkeypatch, latest: bool) -> None:
    calls = []
    monkeypatch.setattr(skill_source, "_run_git", lambda args, **_kwargs: calls.append(args) or f"{COMMIT}\trefs/tags/v0.16.0\n")
    monkeypatch.setattr(skill_source, "release_manifest", lambda tag: None)

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"^v0\.16\.0 has no published skill-manifest\.json; cannot verify$"),
        skill_source.resolve_source(latest=latest, version=None if latest else "0.16.0"),
    ):
        raise AssertionError("an unverifiable release must not resolve")
    assert [args[0] for args in calls] == ["ls-remote"]


def test_bundled_tree_without_a_packaged_manifest_is_refused(monkeypatch, tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(skill_source, "bundled_directory", lambda: plugin)
    monkeypatch.setattr(skill_source, "bundled_manifest", lambda: None)

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"^bundled has no published skill-manifest\.json; cannot verify$"),
        skill_source.resolve_source(),
    ):
        raise AssertionError("an unverifiable bundle must not resolve")


def test_bundled_manifest_is_the_file_beside_the_module(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    module = tmp_path / "atm_bd_console" / "skill_source.py"
    module.parent.mkdir()
    monkeypatch.setattr(skill_source, "__file__", str(module))
    monkeypatch.setattr(skill_source, "_http_get", lambda url: (_ for _ in ()).throw(AssertionError("network")))
    assert skill_source.bundled_manifest() is None
    (module.parent / "skill-manifest.json").write_bytes(PUBLISHED)
    assert skill_source.bundled_manifest() == PUBLISHED


def _sdist(member: str | None, data: bytes = PUBLISHED) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if member is not None:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize(("response", "expected"), [
    (_sdist("atm_bd-0.17.4/src/atm_bd_console/skill-manifest.json", b'{"version":"0.17.4"}'), b'{"version":"0.17.4"}'),
    (_sdist("atm_bd-0.17.4/src/atm_bd_console/other.json"), None),
    (_sdist(None), None),
    (None, None),
], ids=["published", "sdist-without-manifest", "empty-sdist", "no-asset"])
def test_release_manifest_reads_the_sdist_asset_of_the_tag(monkeypatch, response, expected) -> None:
    monkeypatch.undo()
    urls = []
    monkeypatch.setattr(skill_source, "_http_get", lambda url: urls.append(url) or response)

    assert skill_source.release_manifest("v0.17.4") == expected
    assert urls == [f"{skill_source.RELEASES}/v0.17.4/atm_bd-0.17.4.tar.gz"]


def test_release_manifest_rejects_an_unreadable_sdist(monkeypatch) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(skill_source, "_http_get", lambda url: b"not a tarball")
    with pytest.raises(skill_source.SkillSourceError, match="atm_bd-0.17.4.tar.gz is not a readable sdist"):
        skill_source.release_manifest("v0.17.4")


def test_release_manifest_rejects_a_version_that_does_not_match_its_tag(monkeypatch) -> None:
    manifest = b'{"version":"0.17.3"}'
    archive = _sdist("atm_bd-0.17.4/src/atm_bd_console/skill-manifest.json", manifest)
    monkeypatch.undo()
    monkeypatch.setattr(skill_source, "_http_get", lambda _url: archive)

    with pytest.raises(skill_source.SkillSourceError, match="version '0.17.3' does not match release version '0.17.4'"):
        skill_source.release_manifest("v0.17.4")


@pytest.mark.parametrize("manifest", [b'{}', b'{"version":null}', b'{"version":17}', b'[]'],
                         ids=["missing", "null", "number", "non-object"])
def test_release_manifest_rejects_missing_or_invalid_version_with_tag(monkeypatch, manifest: bytes) -> None:
    archive = _sdist("atm_bd-0.17.4/src/atm_bd_console/skill-manifest.json", manifest)
    monkeypatch.undo()
    monkeypatch.setattr(skill_source, "_http_get", lambda _url: archive)

    with pytest.raises(skill_source.SkillSourceError, match=r"v0\.17\.4:.*version.*0\.17\.4"):
        skill_source.release_manifest("v0.17.4")


@pytest.mark.parametrize(("response", "expected"), [
    (b'{"tag_name": "v0.17.4"}', "v0.17.4"),
    (None, None),
], ids=["release", "no-release"])
def test_latest_release_tag_reads_releases_latest(monkeypatch, response, expected) -> None:
    monkeypatch.undo()
    urls = []
    monkeypatch.setattr(skill_source, "_http_get", lambda url: urls.append(url) or response)

    assert skill_source.latest_release_tag() == expected
    assert urls == [skill_source.LATEST_RELEASE_API]


@pytest.mark.parametrize("response", [b"[]", b"not json", b'{"tag_name": "main"}', b'{"tag_name": null}'])
def test_latest_release_tag_rejects_a_malformed_release(monkeypatch, response) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(skill_source, "_http_get", lambda url: response)
    with pytest.raises(skill_source.RepositoryError, match=re.escape(skill_source.LATEST_RELEASE_API)):
        skill_source.latest_release_tag()


def test_http_get_maps_404_to_none_and_other_failures_to_repository_errors(monkeypatch) -> None:
    import urllib.error

    def fail(code):
        def urlopen(request, timeout):
            assert timeout == skill_source.HTTP_TIMEOUT_S
            raise urllib.error.HTTPError(request.full_url, code, "status", {}, None)
        return urlopen

    monkeypatch.setattr(skill_source.urllib.request, "urlopen", fail(404))
    assert skill_source._http_get("https://example.invalid/a") is None
    monkeypatch.setattr(skill_source.urllib.request, "urlopen", fail(503))
    with pytest.raises(skill_source.RepositoryError, match=r"https://example.invalid/a: HTTP 503"):
        skill_source._http_get("https://example.invalid/a")
    monkeypatch.setattr(skill_source.urllib.request, "urlopen",
                        lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    with pytest.raises(skill_source.RepositoryError, match=r"https://example.invalid/a: offline"):
        skill_source._http_get("https://example.invalid/a")


def test_unknown_release_lists_available_tags_without_cloning(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(skill_source, "_run_git", lambda args, **_kwargs: calls.append(args) or f"{COMMIT}\trefs/tags/v0.16.0\n")

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"unknown version 9\.9\.9; available tags: v0\.16\.0"),
        skill_source.resolve_source(version="9.9.9"),
    ):
        raise AssertionError("unknown version must not resolve")
    assert len(calls) == 1


def test_missing_release_artifact_fails_before_yield_with_app_version(monkeypatch, tmp_path: Path) -> None:
    def run(args, **_kwargs):
        if args[0] == "ls-remote":
            return f"{COMMIT}\trefs/tags/v0.16.0\n"
        if args[0] == "clone":
            plugin = Path(args[-1]) / "plugins" / "atm-bd-orchestration"
            plugin.mkdir(parents=True)
            (plugin / "install.py").touch()
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(skill_source, "_run_git", run)

    with (
        pytest.raises(skill_source.SkillSourceError, match=rf"v0\.16\.0 is not installable by atm-bd {re.escape(__version__)}:.*manifest.yaml"),
        skill_source.resolve_source(version="0.16.0"),
    ):
        raise AssertionError("invalid release tree must not be yielded")


@pytest.mark.parametrize("error_type", [skill_source.SkillSourceError, OSError])
def test_release_tree_errors_use_the_shared_formatter(monkeypatch, error_type) -> None:
    failure = error_type("tree unavailable")
    formatted = []
    monkeypatch.setattr(skill_source, "remote_tags", lambda: ("v0.16.0",))
    monkeypatch.setattr(skill_source, "_run_git", lambda *args, **kwargs: "")

    def reject_tree(*args):
        raise failure

    def format_failure(name, error):
        formatted.append((name, error))
        return "shared formatted error"

    monkeypatch.setattr(skill_source, "_validate_pkg_dir", reject_tree)
    monkeypatch.setattr(skill_source, "install_failure_message", format_failure)

    with (
        pytest.raises(skill_source.SkillSourceError, match="^shared formatted error$") as caught,
        skill_source.resolve_source(version="0.16.0"),
    ):
        pytest.fail("an invalid release tree must not be yielded")
    assert formatted == [("v0.16.0", failure)]
    assert caught.value.__cause__ is failure


def test_errors_raised_while_the_tree_is_in_use_are_not_relabelled(monkeypatch, tmp_path: Path) -> None:
    def run(args, **_kwargs):
        if args[0] == "ls-remote":
            return f"{COMMIT}\trefs/tags/v0.16.0\n"
        if args[0] == "clone":
            make_plugin(Path(args[-1]))
            return ""
        return f"{COMMIT}\n"

    monkeypatch.setattr(skill_source, "_run_git", run)

    with pytest.raises(PermissionError), skill_source.resolve_source(version="0.16.0"):
        raise PermissionError("target is not writable")


def test_clone_gets_the_clone_timeout_and_a_timeout_names_the_clone(monkeypatch) -> None:
    seen: list[tuple[list[str], int]] = []

    def run(argv, fix, *, timeout_s, **_kwargs):
        seen.append((list(argv), timeout_s))
        if argv[1] == "ls-remote":
            return Ok(command.Output(0, f"{COMMIT}\trefs/tags/v0.16.0\n"))
        return Err(Timeout("timeout", tuple(argv), timeout_s, fix))

    monkeypatch.setattr(skill_source.command, "run", run)

    with (
        pytest.raises(skill_source.RepositoryError, match=rf"git clone .* failed: timed out after {skill_source.CLONE_TIMEOUT_S}s"),
        skill_source.resolve_source(version="0.16.0"),
    ):
        raise AssertionError("a timed-out clone must not be yielded")
    clone_timeout = next(timeout for argv, timeout in seen if argv[1] == "clone")
    ls_timeout = next(timeout for argv, timeout in seen if argv[1] == "ls-remote")
    assert clone_timeout == skill_source.CLONE_TIMEOUT_S > command.DEFAULT_TIMEOUT_S
    assert ls_timeout == command.DEFAULT_TIMEOUT_S


def test_network_failure_names_upstream_url_and_git_error(monkeypatch) -> None:
    def fail(_args, **_kwargs):
        raise skill_source.RepositoryError(f"{skill_source.REPOSITORY}: git ls-remote failed: offline")

    monkeypatch.setattr(skill_source, "_run_git", fail)

    with pytest.raises(skill_source.RepositoryError, match="offline") as error:
        skill_source.remote_tags()
    assert skill_source.REPOSITORY in str(error.value)


def test_git_failure_is_a_repository_error_and_keeps_the_upstream_name(monkeypatch) -> None:
    monkeypatch.setattr(
        skill_source.command, "run",
        lambda argv, fix, **_kwargs: Err(skill_source.command.CommandFailed("command_failed", tuple(argv), 128, "denied", fix)),
    )

    with pytest.raises(skill_source.RepositoryError) as error:
        skill_source.remote_tags()
    assert str(error.value) == f"{skill_source.REPOSITORY}: git ls-remote --tags --refs {skill_source.REPOSITORY} failed: denied"


def test_a_tree_problem_is_relabelled_with_the_ref_and_is_not_a_repository_error(monkeypatch) -> None:
    def run(args, **_kwargs):
        if args[0] == "ls-remote":
            return f"{COMMIT}\trefs/tags/v0.16.0\n"
        if args[0] == "clone":
            make_plugin(Path(args[-1]), installer="")
            Path(args[-1], "plugins", "atm-bd-orchestration", "manifest.yaml").unlink()
            return ""
        return f"{COMMIT}\n"

    monkeypatch.setattr(skill_source, "_run_git", run)

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"v0\.16\.0 is not installable by atm-bd .*manifest.yaml") as error,
        skill_source.resolve_source(version="0.16.0"),
    ):
        raise AssertionError("an incomplete tree must not be yielded")
    assert not isinstance(error.value, skill_source.RepositoryError)
