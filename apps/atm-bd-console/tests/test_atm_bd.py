"""D5: dispatch_spec fills the production templates exactly; each spec renders with sc-compose --strict."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atm_bd_console.atm_bd import dispatch_spec, step
from atm_bd_console.result import Err, NotFound, Ok
from atm_bd_console.types import Bead, DispatchContext, PullRequest, RepoConfig, Sprint, Step

TEMPLATES = Path(__file__).resolve().parents[3] / "plugins/atm-bd-orchestration/skills/atm-bd-orchestration/templates"
NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
REPO = RepoConfig("atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", ".claude/project/quality-policy.md",
                  ("req-qa", "arch-qa"), "docs/plans")
SPRINT = Sprint("atm-bh-5", "git and GitHub sources", 5, "sprint/bh-5-git-github-source", "sprint/bh-1-contract",
                "phase-bh", ("atm-bh-1",), False, 2)
PR = PullRequest(148, "https://github.com/randlee/atm-bd-orchestration/pull/148", "fix/bh-5-qa-r1",
                 "sprint/bh-10-textual-tabs", "77543fe", "pending", ())
LAYER_PRS = (PullRequest(140, "https://github.com/randlee/atm-bd-orchestration/pull/140",
                          "sprint/bh-5-git-github-source", "fix/bh-1-qa-r1", "235f3a7", "success", ()),)
COMMIT = "77543fe41def472179cfe4554e7ca27d64eff481"


def bead(bead_id: str, stage: str, parent: str, **metadata) -> Bead:
    return Bead(bead_id, bead_id, "open", ("phase-bh", f"stage:{stage}"), parent, (), None, metadata, NOW, None, None)


# Planted after the real atm-bh-5 beads: the sprint container, its poured group and a poured finding group.
ROOT = Bead("atm-phase-bh", "phase-bh", "open", ("phase-bh",), None, (), None, {}, NOW, None, None)
CONTAINER = bead("atm-bh-5", "sprint", "atm-phase-bh", layer=5, branch=SPRINT.branch, pr_target=SPRINT.target,
                 stack="phase-bh")
DEV = bead("atm-bh-5.group-dev", "dev", "atm-bh-5", sprint="bh-5", sprint_bead="atm-bh-5", layer=5)
SANITY = bead("atm-bh-5.group-sanity", "dev-sanity", "atm-bh-5", dev_bead="atm-bh-5.group-dev",
              branch=SPRINT.branch, worktree="/worktrees/sprint/bh-5-git-github-source")
SPRINT_QA = bead("atm-bh-5.group-qa", "qa", "atm-bh-5", checked_bead="atm-bh-5.group-dev", round=1, sprint="bh-5",
                 sprint_bead="atm-bh-5", branch=SPRINT.branch, worktree="/worktrees/sprint/bh-5-git-github-source")
FIX = bead("atm-bh-5.qa1-f1-r1-fix", "fix", "atm-bh-5", round=1, sprint_bead="atm-bh-5", finding_ref="qa1-f1")
FIX_QA = bead("atm-bh-5.qa1-f1-r1-qa", "qa", "atm-bh-5", checked_bead=FIX.id, round=1, sprint="bh-5",
              sprint_bead="atm-bh-5", branch="fix/bh-5-qa-r1", worktree="/worktrees/fix/bh-5-qa-r1")
PARENTS = {item.id: item for item in (ROOT, CONTAINER, DEV, SANITY, SPRINT_QA, FIX, FIX_QA)}


def mapped(value: Bead) -> Step:
    result = step(value, PARENTS)
    assert isinstance(result, Ok), result
    return result.value


def context(value: Step, pr: PullRequest | None) -> DispatchContext:
    return DispatchContext(value, SPRINT, "atm-phase-bh", "atm-bd-console", "fenix", COMMIT,
                           pr.base if pr else value.target or SPRINT.target, value.worktree or "", pr, LAYER_PRS, REPO)


def expected(kind: str, pr: PullRequest | None) -> tuple[str, dict]:
    pr_fields = {"pr_number": str(pr.number) if pr else "", "pr_url": pr.url if pr else ""}
    base = pr.base if pr else SPRINT.target
    if kind == "sanity":
        return "dev-sanity-template.xml.j2", {
            "task_id": "atm-bh-5.group-sanity", "checked_bead": "atm-bh-5.group-dev", "sprint": "",
            "worktree_path": "/worktrees/sprint/bh-5-git-github-source", "branch": SPRINT.branch, "commit": COMMIT,
            "base": base, **pr_fields, "primary_checkout": "/repo", "lint_command": "just lint", "layer_prs": [140],
        }
    qa_common = {
        "phase": "atm-phase-bh", "phase_feature": "atm-bd-console", "stack": "phase-bh", "layer": 5,
        "policy_path": ".claude/project/quality-policy.md", "reviewers_round1": ["req-qa", "arch-qa"], "layer_prs": [140],
        "sprint_bead": "atm-bh-5", "sprint": "bh-5", "commit": COMMIT, "base": base, **pr_fields,
    }
    if kind == "sprint_qa":
        return "qa-template.xml.j2", {
            "task_id": "atm-bh-5.group-qa", "checked_bead": "atm-bh-5.group-dev", "round": 1,
            "worktree_path": "/worktrees/sprint/bh-5-git-github-source", "branch": SPRINT.branch, **qa_common,
        }
    return "qa-template.xml.j2", {
        "task_id": FIX_QA.id, "checked_bead": FIX.id, "round": 2, "carry_forward": FIX.id,
        "worktree_path": "/worktrees/fix/bh-5-qa-r1", "branch": "fix/bh-5-qa-r1", **qa_common,
    }


STEPS = {"sanity": SANITY, "sprint_qa": SPRINT_QA, "fix_qa": FIX_QA}


def render(template: str, values: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("sc-compose")
    assert binary, "sc-compose is required for the D5 render check: install sc-compose >= 1.7.0"
    var_file = tmp_path / "vars.json"
    var_file.write_text(json.dumps(values))
    return subprocess.run(
        (binary, "render", "--strict", "--root", str(TEMPLATES), "--file", str(TEMPLATES / template),
         "--var-file", str(var_file)),
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("pr", [PR, None], ids=["with-pr", "no-pr"])
@pytest.mark.parametrize("kind", list(STEPS))
def test_dispatch_spec_vars_are_exact_and_render_strict(kind: str, pr: PullRequest | None, tmp_path: Path) -> None:
    spec = dispatch_spec(context(mapped(STEPS[kind]), pr))
    assert isinstance(spec, Ok), spec
    template, values = expected(kind, pr)
    assert spec.value.template == template
    assert dict(spec.value.vars) == values
    rendered = render(template, dict(spec.value.vars), tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    verification = f"This is fix verification of fix bead `{FIX.id}`"
    assert (verification in rendered.stdout) is (kind == "fix_qa")


def test_fix_verification_round_is_the_fix_beads_round_plus_one() -> None:
    later = {**PARENTS, FIX.id: bead(FIX.id, "fix", "atm-bh-5", round=2, sprint_bead="atm-bh-5")}
    assert step(FIX_QA, later).value.qa_round == 3
    assert mapped(SPRINT_QA).qa_round == 1


def test_fix_bead_without_round_is_not_found_naming_it() -> None:
    missing = {**PARENTS, FIX.id: bead(FIX.id, "fix", "atm-bh-5", sprint_bead="atm-bh-5")}
    result = step(FIX_QA, missing)
    assert isinstance(result, Err) and result.error == NotFound("not_found", f"{FIX.id}.metadata.round",
                                                               "add the required ATM BD value")


def test_qa_step_without_round_is_not_found_not_round_one() -> None:
    from dataclasses import replace

    result = dispatch_spec(context(replace(mapped(SPRINT_QA), qa_round=None), PR))
    assert isinstance(result, Err) and isinstance(result.error, NotFound)
    assert result.error.what == "atm-bh-5.group-qa.metadata.round"
