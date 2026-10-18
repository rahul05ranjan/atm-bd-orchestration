import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow_step(identity):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    return next(step for step in workflow["jobs"]["gate-and-tag"]["steps"] if step.get("id") == identity)


@pytest.mark.parametrize("target,mode,reference", [("testpypi", "readiness", "branch-sha"),
                                                 ("production", "final", "origin/main")])
def test_gate_selects_dispatch_ref_only_for_rehearsal(tmp_path, target, mode, reference):
    script_dir = tmp_path / ".github/scripts"
    script_dir.mkdir(parents=True)
    gate = script_dir / "release_gate.sh"
    gate.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$ARGUMENTS"\n')
    gate.chmod(0o755)
    script = workflow_step("release_gate")["run"]
    script = script.replace("${{ steps.meta.outputs.release_tag }}", "v0.17.3")
    script = script.replace("${{ steps.meta.outputs.release_version }}", "0.17.3")
    script = script.replace("${{ steps.build_plan.outputs.workspace_toml }}", "release-version.toml")
    output = tmp_path / "output"
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, check=True, timeout=30,
                   env={**os.environ, "RELEASE_TARGET": target, "DISPATCH_SHA": "branch-sha",
                        "RELEASE_ARTIFACT_MANIFEST": "release/publish-artifacts.toml",
                        "ALREADY_PUBLISHED_CHANNELS": "", "ARGUMENTS": str(tmp_path / "args"),
                        "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)
    assert (tmp_path / "args").read_text().splitlines()[:2] == [mode, reference]
    assert (output.read_text() if output.exists() else "") == (
        "release_sha=branch-sha\n" if target == "testpypi" else "")


def git(repo, *arguments):
    return subprocess.run(["git", *arguments], cwd=repo, check=True, timeout=30,
                          text=True, capture_output=True).stdout.strip()


def test_copied_candidate_step_reuses_provenance_and_rehearsal_does_not_tag_production(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--bare", "--initial-branch=main")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "file").write_text("baseline")
    git(repo, "add", "file")
    git(repo, "commit", "-m", "baseline")
    git(repo, "push", "origin", "main")
    baseline = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "release-fixture")
    (repo / "file").write_text("release packaging fix")
    git(repo, "commit", "-am", "fix")
    branch = git(repo, "rev-parse", "HEAD")
    env = {**os.environ, "GITHUB_OUTPUT": str(tmp_path / "output")}
    candidate = workflow_step("candidate")["run"].replace("${{ github.event.inputs.version }}", "0.17.3")
    for _attempt in range(2):
        subprocess.run(["bash", "-e", "-c", candidate], cwd=repo, env=env, check=True,
                       capture_output=True, text=True, timeout=30)
        assert git(repo, "rev-parse", "release-candidate-v0.17.3^{commit}") == baseline
    script = workflow_step("release-ref")["run"]
    script = script.replace("${{ steps.meta.outputs.release_tag }}", "v0.17.3")
    script = script.replace("${{ steps.meta.outputs.release_target }}", "testpypi")
    script = script.replace("${{ steps.release_gate.outputs.release_sha }}", branch)
    subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env, check=True,
                   capture_output=True, text=True, timeout=30)
    assert f"build_ref={branch}" in Path(env["GITHUB_OUTPUT"]).read_text()
    assert git(repo, "ls-remote", "--tags", "origin", "refs/tags/v0.17.3") == ""
