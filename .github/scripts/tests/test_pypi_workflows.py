import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_publish_uses_release_assets_and_separate_targets():
    workflow = yaml.safe_load((ROOT / ".github/workflows/pypi-publish.yml").read_text())
    assert workflow[True]["workflow_dispatch"]["inputs"]["target"]["options"] == ["testpypi", "production"]
    assert workflow["jobs"]["publish"]["needs"] == "verify-release"
    steps = workflow["jobs"]["publish"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)
    assert "gh release download" in script
    assert "verify-python-release-assets" in script
    assert "python -m twine upload" in script
    assert "--skip-existing" in script
    assert "uv publish" not in script
    assert "python -m build" not in script


def test_release_jobs_preserve_tagging_and_rehearsal_order():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]
    assert set(jobs) == {"gate-and-tag", "release-plan", "build-python-wheels",
                         "build-python-sdists", "publish-testpypi", "release"}
    for job in jobs.values():
        dependencies = job.get("needs", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        assert set(dependencies) <= set(jobs)
    gate = jobs["gate-and-tag"]["steps"]
    assert next(step for step in gate if step.get("id") == "candidate")["if"] == "${{ inputs.target == 'testpypi' }}"
    tag = next(step for step in gate if step.get("id") == "release-ref")["run"]
    assert 'git tag "$tag" "$main_sha"' in tag
    assert 'if [[ "$target" == "production" ]]' in tag
    rehearsal = jobs["publish-testpypi"]
    assert "release_target == 'testpypi'" in rehearsal["if"]
    assert rehearsal["environment"] == "testpypi"
    assert "release_target == 'production'" in jobs["release"]["if"]
    script = "\n".join(step.get("run", "") for step in rehearsal["steps"])
    assert "twine==6.1.0" in script and "twine upload" in script
    assert "gh release" not in script
    assert jobs["release"]["steps"][-1]["run"].endswith("--finalized")


def test_ci_checks_both_artifacts_with_the_publisher_twine():
    workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())
    checks = [step["run"] for step in workflow["jobs"]["console-tests"]["steps"]
              if "python -m twine check" in step.get("run", "")]
    assert len(checks) == 1
    assert "python -m build --wheel --sdist" in checks[0]
    assert "--with twine==6.1.0" in checks[0]
    assert "*.whl" in checks[0] and "*.tar.gz" in checks[0]


@pytest.mark.parametrize("mode,expected", [("absent", 1), ("auth", 1), ("complete", 0)])
def test_copied_release_verifier_fails_closed(tmp_path, mode, expected):
    action = yaml.safe_load((ROOT / ".github/actions/verify-published-release/action.yml").read_text())
    executable = tmp_path / "gh"
    executable.write_text("#!/bin/sh\ncase \"$PROBE_FIXTURE\" in\nabsent) echo 'HTTP 404' >&2; exit 1;;\nauth) echo 'HTTP 403' >&2; exit 1;;\ncomplete) echo '{\"isDraft\":false,\"assets\":[{\"name\":\"fixture.whl\"}]}' ;;\nesac\n")
    executable.chmod(0o755)
    result = subprocess.run(["bash", "-c", action["runs"]["steps"][0]["run"]],
                            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                                 "PROBE_FIXTURE": mode, "RELEASE_TAG": "v0.17.3",
                                 "RELEASE_REPOSITORY": "fixture/repo", "PROBE_MODE": "false",
                                 "REQUIRED_ASSET_PATTERNS": r"\.whl$", "GITHUB_OUTPUT": str(tmp_path / "outputs")},
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == expected, result.stderr
