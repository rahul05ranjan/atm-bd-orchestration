from __future__ import annotations

from pathlib import Path

import pytest

from atm_bd_console import skill_source

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

    def run(args):
        calls.append(args)
        return (
            "b\trefs/tags/v0.9.0\n"
            "a\trefs/tags/v0.16.0\n"
            "c\trefs/tags/v0.10.0\n"
            "ignored\trefs/heads/main\n"
        )

    monkeypatch.setattr(skill_source, "_run_git", run)

    assert skill_source.list_tags() == ("v0.10.0", "v0.16.0", "v0.9.0")
    assert calls == [["ls-remote", "--tags", "--refs", skill_source.REPOSITORY]]


def test_bundled_source_needs_no_network_and_uses_packaged_data(monkeypatch, tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(skill_source, "bundled_directory", lambda: plugin)
    monkeypatch.setattr(skill_source, "_run_git", lambda *_: (_ for _ in ()).throw(AssertionError("network/git")))

    with skill_source.resolve_source() as source:
        assert source.name == "bundled"
        assert source.pkg_dir == plugin
        assert source.commit == "unknown"


def test_release_resolves_to_shallow_clone_as_data_without_running_it(monkeypatch, tmp_path: Path) -> None:
    calls = []
    cloned = []

    def run(args):
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

    def run(args):
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
    monkeypatch.setattr(skill_source, "_run_git", lambda args: calls.append(args) or f"{COMMIT}\trefs/tags/v0.16.0\n")

    with (
        pytest.raises(skill_source.SkillSourceError, match=r"unknown version 9\.9\.9; available tags: v0\.16\.0"),
        skill_source.resolve_source(version="9.9.9"),
    ):
        raise AssertionError("unknown version must not resolve")
    assert len(calls) == 1


def test_missing_release_artifact_fails_before_yield_with_app_version(monkeypatch, tmp_path: Path) -> None:
    def run(args):
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
        pytest.raises(skill_source.SkillSourceError, match=r"v0\.16\.0 is not installable by atm-bd 0\.17\.0:.*manifest.yaml"),
        skill_source.resolve_source(version="0.16.0"),
    ):
        raise AssertionError("invalid release tree must not be yielded")


def test_network_failure_names_upstream_url_and_git_error(monkeypatch) -> None:
    def fail(_args):
        raise skill_source.SkillSourceError(f"{skill_source.REPOSITORY}: git ls-remote failed: offline")

    monkeypatch.setattr(skill_source, "_run_git", fail)

    with pytest.raises(skill_source.SkillSourceError, match="offline") as error:
        skill_source.list_tags()
    assert skill_source.REPOSITORY in str(error.value)
