"""Guard the release workflow's same-run artifact and approval contract."""

import hashlib
import io
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from process_helpers import run_process

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
    assert jobs["pypi"]["if"] == "inputs.publish_to_pypi && github.ref == 'refs/heads/main'"
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
    assert run_process("legacy command absent", ["bash", "-c", check], env=env, check=False).returncode == 0
    (tmp_path / "atm-bd-console").touch()
    assert run_process("legacy command present", ["bash", "-c", check], env=env, check=False).returncode != 0


def test_workflow_jobs_have_explicit_timeouts():
    jobs = _workflow()["jobs"]
    tests_workflow = yaml.safe_load((WORKFLOW.parent / "tests.yml").read_text(encoding="utf-8"))
    for job in (jobs["build"], jobs["pypi"], tests_workflow["jobs"]["console-tests"]):
        assert 0 < job["timeout-minutes"] <= 30


@pytest.mark.parametrize("pypi_status,testpypi_status,success", [(404, 404, True), (200, 404, False), (404, 200, False), (500, 404, False)])
def test_rehearsal_checks_both_registry_versions(tmp_path, pypi_status, testpypi_status, success):
    steps = _workflow()["jobs"]["testpypi"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Check release version availability before rehearsal")
    curl = tmp_path / "curl"
    curl.write_text(
        '#!/bin/bash\nurl="${!#}"\nprintf "%s\\n" "$url" >> "$URL_LOG"\n'
        'case "$url" in https://pypi.org/*) printf "%s" "$PYPI_STATUS";; '
        'https://test.pypi.org/*) printf "%s" "$TESTPYPI_STATUS";; *) exit 1;; esac\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    log = tmp_path / "urls"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "URL_LOG": str(log),
           "PYPI_STATUS": str(pypi_status), "TESTPYPI_STATUS": str(testpypi_status)}
    result = run_process("release version availability check", ["bash", "-c", script], cwd=WORKFLOW.parents[2], env=env, check=False)
    assert (result.returncode == 0) is success
    with (WORKFLOW.parents[2] / "apps/atm-bd-console/pyproject.toml").open("rb") as source:
        version = tomllib.load(source)["project"]["version"]
    urls = log.read_text(encoding="utf-8").splitlines()
    assert urls[0] == f"https://pypi.org/pypi/atm-bd/{version}/json"
    if pypi_status == 404:
        assert urls[1] == f"https://test.pypi.org/pypi/atm-bd/{version}/json"


@pytest.mark.parametrize("pypi_status", [404, 200, 500])
def test_production_checks_exact_version_after_verifying_rehearsed_files(tmp_path, monkeypatch, pypi_status):
    steps = _workflow()["jobs"]["pypi"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Verify identical rehearsed files and production version availability")
    code = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    files = [tmp_path / "atm_bd-1.2.3-py3-none-any.whl", tmp_path / "atm_bd-1.2.3.tar.gz"]
    for index, file in enumerate(files):
        file.write_bytes(f"distribution-{index}".encode())
    uploaded = {"urls": [{"filename": file.name, "digests": {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}} for file in files]}
    seen = []

    def urlopen(url):
        seen.append(url)
        if url == "https://test.pypi.org/pypi/atm-bd/1.2.3/json":
            return io.BytesIO(json.dumps(uploaded).encode())
        assert url == "https://pypi.org/pypi/atm-bd/1.2.3/json"
        if pypi_status != 200:
            raise urllib.error.HTTPError(url, pypi_status, "fixture", {}, None)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sys, "argv", ["workflow", str(tmp_path)])
    # Execute the checked-in workflow's embedded script with controlled HTTP responses.
    if pypi_status == 404:
        exec(code, {})  # noqa: S102
    else:
        with pytest.raises((AssertionError, SystemExit)):
            exec(code, {})  # noqa: S102
    assert seen == ["https://test.pypi.org/pypi/atm-bd/1.2.3/json", "https://pypi.org/pypi/atm-bd/1.2.3/json"]
