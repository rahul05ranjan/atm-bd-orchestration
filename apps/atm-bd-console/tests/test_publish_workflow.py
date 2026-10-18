"""Guard the release workflow's same-run artifact and approval contract."""

import os
import re
import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/publish.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _artifact_steps(job, action):
    return [step for step in job["steps"] if step.get("uses", "").startswith(f"actions/{action}-artifact@")]


def test_release_jobs_share_one_build_artifact_and_run():
    workflow = _workflow()
    # PyYAML's YAML 1.1 parser represents the key "on" as True.
    assert set(workflow[True]) == {"workflow_dispatch"}
    jobs = workflow["jobs"]
    assert jobs["testpypi"]["needs"] == "build"
    assert jobs["pypi"]["needs"] == "testpypi"
    assert jobs["pypi"]["if"] == "inputs.publish_to_pypi"
    assert workflow[True]["workflow_dispatch"]["inputs"]["publish_to_pypi"]["default"] is False
    upload, = _artifact_steps(jobs["build"], "upload")
    rehearsal, = _artifact_steps(jobs["testpypi"], "download")
    production, = _artifact_steps(jobs["pypi"], "download")
    assert upload["with"]["name"] == rehearsal["with"]["name"] == production["with"]["name"]
    assert "run-id" not in rehearsal["with"] and "run-id" not in production["with"]
    assert sum("uv build " in step.get("run", "") for job in jobs.values() for step in job["steps"]) == 1
    rehearsal_script = "\n".join(step.get("run", "") for step in jobs["testpypi"]["steps"])
    production_script = "\n".join(step.get("run", "") for step in jobs["pypi"]["steps"])
    assert "--check-url https://test.pypi.org/simple/" in rehearsal_script
    assert '"$RUNNER_TEMP"/rehearsed-dist/*' in rehearsal_script
    assert '"$RUNNER_TEMP"/dist/*' in production_script
    assert "hashlib.sha256" in rehearsal_script and "== uploaded" in rehearsal_script
    assert "hashlib.sha256" in production_script and "== rehearsed" in production_script
    assert "bundled @ $GITHUB_SHA (atm-bd $version)" in rehearsal_script


def test_release_workflow_is_serialized_and_actions_are_pinned():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])


def test_rehearsal_checks_that_legacy_command_is_absent(tmp_path):
    steps = _workflow()["jobs"]["testpypi"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Install from TestPyPI and exercise entry points")
    lines = script.splitlines()
    start = lines.index("legacy_command=atm-bd")
    check = "\n".join(lines[start : start + 2])
    env = {**os.environ, "UV_TOOL_BIN_DIR": str(tmp_path)}
    assert subprocess.run(["bash", "-c", check], env=env, check=False).returncode == 0
    (tmp_path / "atm-bd-console").touch()
    assert subprocess.run(["bash", "-c", check], env=env, check=False).returncode != 0
