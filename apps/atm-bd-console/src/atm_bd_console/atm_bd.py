from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .result import Err, NotFound, Ok, ParseFailed, Result
from .types import Bead, DispatchContext, DispatchSpec, Phase, PhaseFile, RepoConfig, Sprint, Step


def _missing(what: str) -> Err:
    return Err(NotFound("not_found", what, "add the required ATM BD value"))


def phase_file(repo_root: str, x: str) -> Result[PhaseFile]:
    path = Path(repo_root) / ".atm-bd" / f"phase-{x}.toml"
    try:
        raw = tomllib.loads(path.read_text())
        return Ok(PhaseFile(x, raw["root_id"], raw["label"], tuple(raw["sprints"]), {key: tuple(value) for key, value in raw.get("depends_on", {}).items()}))
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        return Err(ParseFailed("parse_failed", str(path), str(error), "repair phase file"))


def repo_config(repo_root: str) -> Result[RepoConfig]:
    path = Path(repo_root) / ".claude/project/atm-bd-orchestration.yaml"
    try:
        values: dict[str, Any] = {}
        for line in path.read_text().splitlines():
            if ":" in line and not line.startswith((" ", "#")):
                key, value = line.split(":", 1); values[key.strip()] = value.strip().strip('"')
        keys = ("bead_prefix", "dev_sanity_member", "qa_member", "primary_checkout", "worktree_base", "lint_command", "policy_path", "reviewers_round1", "plans_dir")
        for key in keys:
            if not values.get(key): return _missing(key)
        reviewers = tuple(part.strip() for part in values["reviewers_round1"].strip("[]").split(",") if part.strip())
        return Ok(RepoConfig(values["bead_prefix"], values["dev_sanity_member"], values["qa_member"], values["primary_checkout"], values["worktree_base"], values["lint_command"], values["policy_path"], reviewers, values["plans_dir"]))
    except OSError as error:
        return Err(ParseFailed("parse_failed", str(path), str(error), "repair repository config"))


def step(bead: Bead, parents: Mapping[str, Bead]) -> Result[Step]:
    metadata = bead.metadata
    stage = next((label.removeprefix("stage:") for label in bead.labels if label.startswith("stage:")), "other")
    kind = {"dev-sanity": "sanity", "qa": "qa", "plan-review": "plan_review"}.get(stage, stage if stage in {"dev", "fix", "finding"} else "other")
    checked = metadata.get("dev_bead") if kind == "sanity" else metadata.get("checked_bead") if kind == "qa" else None
    if kind in {"sanity", "qa"} and not checked: return _missing(f"{bead.id}.metadata.checked_bead")
    if kind == "qa" and metadata.get("round") is None: return _missing(f"{bead.id}.metadata.round")
    sprint = metadata.get("sprint")
    sprint_bead = metadata.get("sprint_bead")
    if kind == "qa" and not sprint_bead and checked in parents: sprint_bead = parents[checked].metadata.get("sprint_bead")
    if kind == "qa" and not sprint_bead: return _missing(f"{bead.id}.metadata.sprint_bead")
    parent = parents.get(bead.parent or "")
    top = parent.id if parent and parent.parent is None else None
    carry = checked if kind == "qa" and stage == "qa" and ("stage:fix" in parents.get(checked, bead).labels or "stage:finding" in parents.get(checked, bead).labels) else None
    return Ok(Step(bead.id, sprint, kind, bool(metadata.get("quick_fix")), "anchor" in bead.labels, bead.status, False, bead.assignee, metadata.get("branch"), metadata.get("pr_target"), metadata.get("worktree"), metadata.get("layer"), metadata.get("difficulty"), (top or bead.id).removeprefix(metadata.get("bead_prefix", "")), top, bead.claimed_at, bead.closed_at, bead.title, checked, sprint_bead, metadata.get("round"), carry))


def phase(file: PhaseFile, root: Bead, open_beads: tuple[Bead, ...], ready: tuple[Bead, ...], shown: tuple[Bead, ...]) -> Result[Phase]:
    by_id = {bead.id: bead for bead in shown + open_beads + ready}
    sprints = []
    for sprint_id in file.sprints:
        bead = by_id.get(sprint_id)
        if bead is None: return _missing(sprint_id)
        meta = bead.metadata; sprints.append(Sprint(bead.id, bead.title, meta["layer"], meta["branch"], meta["pr_target"], file.depends_on.get(bead.id, ()), bead.status == "closed", sum(1 for item in by_id.values() if item.parent == bead.id and "stage:finding" in item.labels)))
    steps, errors = [], []
    for bead in by_id.values():
        if bead.id in file.sprints or bead.id == root.id: continue
        mapped = step(bead, by_id)
        if isinstance(mapped, Err): errors.append((bead.id, mapped.error))
        else: steps.append(mapped.value)
    return Ok(Phase(root.id, file.label, root.title, root.status == "closed", tuple(sprints), tuple(steps), tuple(errors)))


def dispatch_spec(ctx: DispatchContext) -> Result[DispatchSpec]:
    step = ctx.step; pr = ctx.pr
    common = {"task_id": step.bead_id, "checked_bead": step.checked_bead or "", "sprint": step.sprint or "", "worktree_path": ctx.worktree_path, "branch": step.branch or "", "commit": ctx.commit, "base": ctx.base, "pr_number": str(pr.number) if pr else "", "pr_url": pr.url if pr else ""}
    if step.kind == "sanity":
        return Ok(DispatchSpec("dev-sanity-template.xml.j2", common | {"primary_checkout": ctx.repo.primary_checkout, "lint_command": ctx.repo.lint_command, "layer_prs": ctx.layer_prs}))
    if step.kind == "qa":
        values = common | {"phase": ctx.phase_id, "phase_feature": ctx.phase_title, "stack": ctx.stack, "layer": ctx.sprint.layer, "round": step.qa_round or 1, "policy_path": ctx.repo.policy_path, "reviewers_round1": ctx.repo.reviewers_round1, "layer_prs": ctx.layer_prs, "sprint_bead": step.sprint_bead or ""}
        if step.carry_forward: values["carry_forward"] = step.carry_forward
        return Ok(DispatchSpec("qa-template.xml.j2", values))
    return _missing(f"dispatch template for {step.kind}")
