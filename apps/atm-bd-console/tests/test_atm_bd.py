"""D5: dispatch_spec fills the production templates exactly; each spec renders with sc-compose --strict."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from atm_bd_console import beads_source
from atm_bd_console.atm_bd import dispatch_spec, group_suffix, phase, phase_file, step
from atm_bd_console.result import ConfigInvalid, Err, NotFound, Ok
from atm_bd_console.types import (
    Bead,
    DispatchContext,
    Phase,
    PhaseFile,
    PullRequest,
    RepoConfig,
    Sprint,
    Step,
    StepKind,
)

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


# D4 over recorded and planted beads, read through beads_source from a fake bd.
# show-findings.json: `bd show` of the real atm-bh-7 finding group qa1-f1-r1 (fix, sanity, QA) and the important
# finding atm-bh-7.group-qa-f10 with its sanity and qa-bead.json.j2 QA (no sprint_bead), atm-core 2026-10-15.
# planted.json: a foreign epic with a container and a quick-fix anchor dev step, a plan review, a dev whose
# parent is not shown, and two QA beads missing round or checked_bead.
BEADS = Path(__file__).parent / "fixtures" / "beads"


def recorded(fake_bin, monkeypatch: pytest.MonkeyPatch, *names: str) -> tuple[Bead, ...]:
    """Each file replayed as one `bd show` reply."""
    fake_bin.make("bd", f'cat "{BEADS}/$FAKE_FILE"\n')
    beads: list[Bead] = []
    for name in names:
        monkeypatch.setenv("FAKE_FILE", name)
        result = beads_source.show(("replayed",))
        assert isinstance(result, Ok), result
        beads.extend(result.value)
    return tuple(beads)


@pytest.fixture
def d4(fake_bin, monkeypatch: pytest.MonkeyPatch) -> tuple[PhaseFile, Phase, dict[str, Bead]]:
    open_beads = recorded(fake_bin, monkeypatch, "list-open.json")
    shown = recorded(fake_bin, monkeypatch, "show-closed.json", "show-findings.json", "planted.json")
    file = phase_file(str(BEADS / "repo"), "bh").value
    root = next(item for item in open_beads if item.id == "atm-phase-bh")
    result = phase(file, root, open_beads, (), shown)
    assert isinstance(result, Ok), result
    return file, result.value, {item.id: item for item in shown + open_beads}


def test_planted_steps_map_to_each_step_kind(d4) -> None:
    _, mapped_phase, _ = d4
    kinds = {item.bead_id: item.kind for item in mapped_phase.steps}
    assert {kinds[bead_id] for bead_id in (
        "atm-bh-7.group-dev", "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7.qa1-f1-r1-sanity", "atm-bh-7.qa1-f1-r1-qa",
        "atm-bh-7.group-qa-f10", "atm-bh-7.plan-review", "zz-other-1", "zz-phase-other",
    )} == set(get_args(StepKind))
    assert (kinds["atm-bh-7.group-dev"], kinds["atm-bh-7.qa1-f1-r1-fix"], kinds["atm-bh-7.qa1-f1-r1-sanity"],
            kinds["atm-bh-7.qa1-f1-r1-qa"], kinds["atm-bh-7.group-qa-f10"], kinds["atm-bh-7.plan-review"],
            kinds["zz-other-1"], kinds["zz-phase-other"]) == (
        "dev", "fix", "sanity", "qa", "finding", "plan_review", "container", "other")


def test_foreign_top_epic_is_kept_and_a_broken_chain_is_none(d4) -> None:
    _, mapped_phase, _ = d4
    steps = {item.bead_id: item for item in mapped_phase.steps}
    foreign = steps["zz-other-1.group-dev"]
    assert (foreign.top_epic, foreign.tag) == ("zz-phase-other", "phase-other")
    assert steps["atm-bh-7.qa1-f1-r1-fix"].top_epic == "atm-phase-bh"
    assert steps["atm-bh-7.orphan-dev"].top_epic is None


def test_quick_fix_and_anchor_are_set(d4) -> None:
    _, mapped_phase, _ = d4
    steps = {item.bead_id: item for item in mapped_phase.steps}
    assert (steps["zz-other-1.group-dev"].quick_fix, steps["zz-other-1.group-dev"].anchor) == (True, True)
    assert (steps["atm-bh-7.group-dev"].quick_fix, steps["atm-bh-7.group-dev"].anchor) == (False, False)


def test_finding_group_qa_carries_its_fix_bead(d4) -> None:
    _, mapped_phase, _ = d4
    qa = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.qa1-f1-r1-qa")
    assert (qa.checked_bead, qa.carry_forward, qa.sprint_bead, qa.qa_round) == (
        "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7.qa1-f1-r1-fix", "atm-bh-7", 2)
    sanity = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.qa1-f1-r1-sanity")
    assert sanity.checked_bead == "atm-bh-7.qa1-f1-r1-fix"


def test_qa_bead_template_qa_gets_sprint_bead_from_its_finding(d4) -> None:
    _, mapped_phase, beads = d4
    assert "sprint_bead" not in beads["atm-bh-7.group-qa-f10-qa"].metadata
    qa = next(item for item in mapped_phase.steps if item.bead_id == "atm-bh-7.group-qa-f10-qa")
    assert (qa.checked_bead, qa.sprint_bead, qa.carry_forward) == (
        "atm-bh-7.group-qa-f10", "atm-bh-7", "atm-bh-7.group-qa-f10")


def test_qa_without_round_or_checked_bead_is_a_step_error_and_every_other_step_is_present(d4) -> None:
    file, mapped_phase, beads = d4
    errors = dict(mapped_phase.step_errors)
    assert errors["atm-bh-7.qa-no-round"] == NotFound("not_found", "atm-bh-7.qa-no-round.metadata.round",
                                                      "add the required ATM BD value")
    assert errors["atm-bh-7.qa-no-checked"] == NotFound("not_found", "atm-bh-7.qa-no-checked.metadata.checked_bead",
                                                        "add the required ATM BD value")
    step_ids = {item.bead_id for item in mapped_phase.steps}
    assert not step_ids & {"atm-bh-7.qa-no-round", "atm-bh-7.qa-no-checked"}
    others = set(beads) - set(errors) - set(file.sprints) - {"atm-phase-bh"}
    assert step_ids == others


@pytest.mark.parametrize("kind", ["dev", "fix", "finding", "plan_review", "container", "other"])
def test_unsupported_kind_is_config_invalid_not_a_missing_value(kind: str) -> None:
    from dataclasses import replace

    result = dispatch_spec(context(replace(mapped(SANITY), kind=kind), PR))
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert (result.error.key, result.error.value) == ("atm-bh-5.group-sanity.kind", kind)


def test_group_suffix_names_only_poured_group_beads() -> None:
    assert [group_suffix(bead_id) for bead_id in (
        "atm-bh-5.group-dev", "atm-bh-5.group-sanity", "atm-bh-5.group-qa", "atm-bh-5.group-qa-f1", "atm-bh-5",
    )] == ["dev", "sanity", "qa", None, None]
