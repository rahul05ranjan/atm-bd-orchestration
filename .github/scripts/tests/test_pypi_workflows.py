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


def test_release_subset_builds_both_distributions_and_checks_immutability():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert set(workflow["jobs"]) == {"build-python", "release"}
    script = "\n".join(step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"])
    assert "python -m build --wheel --outdir dist 'apps/atm-bd-console'" in script
    assert "python -m build --sdist --outdir dist 'apps/atm-bd-console'" in script
    assert "git merge-base --is-ancestor" in script
    assert "--finalized" in script
    assert "release_gate.sh" not in script
    assert "npm_release.py" not in script
    assert "git tag " not in script
    assert "git push " not in script
    steps = workflow["jobs"]["release"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("softprops/action-gh-release@"))
    assert upload["with"]["files"] == "release/*\n"
    assert steps[-1]["run"].endswith('--finalized')


@pytest.mark.parametrize("mode,expected", [("absent", 1), ("auth", 1), ("complete", 0)])
def test_copied_release_verifier_fails_closed(tmp_path, mode, expected):
    action = yaml.safe_load((ROOT / ".github/actions/verify-published-release/action.yml").read_text())
    executable = tmp_path / "gh"
    executable.write_text("#!/bin/sh\ncase \"$PROBE_FIXTURE\" in\nabsent) echo 'HTTP 404' >&2; exit 1;;\nauth) echo 'HTTP 403' >&2; exit 1;;\ncomplete) echo '{\"isDraft\":false,\"assets\":[{\"name\":\"fixture.whl\"}]}' ;;\nesac\n")
    executable.chmod(0o755)
    result = subprocess.run(["bash", "-c", action["runs"]["steps"][0]["run"]],
                            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                                 "PROBE_FIXTURE": mode, "RELEASE_TAG": "v0.17.2",
                                 "RELEASE_REPOSITORY": "fixture/repo", "PROBE_MODE": "false",
                                 "REQUIRED_ASSET_PATTERNS": r"\.whl$", "GITHUB_OUTPUT": str(tmp_path / "outputs")},
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == expected, result.stderr
