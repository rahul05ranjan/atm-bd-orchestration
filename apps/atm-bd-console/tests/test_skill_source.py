from __future__ import annotations

from pathlib import Path

import pytest

from atm_bd_console import command, skill_source
from atm_bd_console.result import Err, Ok, Timeout

COMMIT = "0123456789abcdef0123456789abcdef01234567"


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


def test_latest_clones_main_and_reports_its_commit(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        if args[0] == "ls-remote":
            return ""
        if args[0] == "clone":
            make_plugin(Path(args[-1]))
            return ""
        if args[0] == "-C":
            return COMMIT
        raise AssertionError(args)

    monkeypatch.setattr(skill_source, "_run_git", run)

    with skill_source.resolve_source(latest=True) as source:
        assert source.name == "main"
        assert source.commit == COMMIT
    assert calls[1][5] == "main"


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
        pytest.raises(skill_source.SkillSourceError, match=r"v0\.16\.0 is not installable by atm-bd 0\.17\.1:.*manifest.yaml"),
        skill_source.resolve_source(version="0.16.0"),
    ):
        raise AssertionError("invalid release tree must not be yielded")


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
