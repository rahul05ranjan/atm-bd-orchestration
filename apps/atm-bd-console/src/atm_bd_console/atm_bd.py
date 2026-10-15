from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .ids import short_id
from .result import ConfigInvalid, Err, Error, IoFailed, NotFound, Ok, ParseFailed, Result
from .types import Bead, DispatchContext, DispatchSpec, Phase, PhaseFile, RepoConfig, Sprint, Step, StepKind

# atm-bd conventions (atm-beads sprint_index_common.py): the only place they are named.
PHASE_DIR = ".atm-bd"
PHASE_KEYS = ("plan", "root", "integration_branch")
PLAN_KEYS = frozenset({"sprint", "depends_on"})
PHASE_ID = re.compile(r"[a-z0-9]+")
SPRINT_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
GROUP_MARK = ".group-"
GROUP_SUFFIXES = ("dev", "sanity", "qa")
SPRINT_META = ("layer", "branch", "pr_target", "stack")
STAGE_PREFIX = "stage:"
FINDING_LABEL = f"{STAGE_PREFIX}finding"
FIX_LABEL = f"{STAGE_PREFIX}fix"
ANCHOR_LABEL = "anchor"
# stage:<stage> label -> StepKind; a bead without a stage label takes the stage of its group suffix.
STAGE_KINDS: Mapping[str, StepKind] = {
    "dev": "dev", "fix": "fix", "finding": "finding", "dev-sanity": "sanity", "qa": "qa",
    "plan-review": "plan_review", "sprint": "container",
}
SUFFIX_STAGES = dict(zip(GROUP_SUFFIXES, ("dev", "dev-sanity", "qa"), strict=True))
# Step metadata keys (formulas sprint-group/finding-group, qa-bead.json.j2, finding-bead.json.j2).
CHECKED_KEYS: Mapping[StepKind, str] = {"sanity": "dev_bead", "qa": "checked_bead"}
ROUND_KEY = "round"
SPRINT_KEY = "sprint"
SPRINT_BEAD_KEY = "sprint_bead"
QUICK_FIX_KEY = "quick_fix"
BRANCH_KEY, PR_TARGET_KEY, WORKTREE_KEY, LAYER_KEY, DIFFICULTY_KEY = "branch", "pr_target", "worktree", "layer", "difficulty"
# The production template each dispatched StepKind fills.
TEMPLATES: Mapping[StepKind, str] = {"sanity": "dev-sanity-template.xml.j2", "qa": "qa-template.xml.j2"}
REPO_CONFIG_PATH = ".claude/project/atm-bd-orchestration.yaml"
# RepoConfig field -> key in the repository configuration file; primary_checkout is repo_root (per-machine path).
REPO_KEYS = (
    ("bead_prefix", "bead_prefix"), ("sanity_member", "dev_sanity_member"), ("qa_member", "qa_member"),
    ("worktree_base", "worktree_base"), ("lint_command", "lint_command"),
    ("policy_path", "policy_path"), ("reviewers_round1", "reviewers_round1"), ("plans_dir", "plans_dir"),
)


def _missing(what: str) -> Err:
    return Err(NotFound("not_found", what, "add the required ATM BD value"))


def _read(path: Path, fix: str) -> Result[str]:
    try:
        return Ok(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Err(NotFound("not_found", str(path), fix))
    except (OSError, UnicodeDecodeError) as error:
        return Err(IoFailed("io_failed", str(path), str(error), f"make {path} readable"))


def _phase_label(x: str) -> str:
    return f"phase-{x}"


def sprint_bead_id(root_id: str, x: str, sprint: str) -> str:
    """The sprint container id `<prefix>-<sprint>`; the prefix is the root id `<prefix>-phase-<x>` without its tail."""
    return f"{root_id.removesuffix(f'-phase-{x}')}-{sprint}"


def group_ids(sprint_id: str) -> tuple[str, ...]:
    """The sprint container's poured group beads `<container>.group-{dev,sanity,qa}`."""
    return tuple(f"{sprint_id}{GROUP_MARK}{suffix}" for suffix in GROUP_SUFFIXES)


def group_suffix(bead_id: str) -> str | None:
    """The poured group suffix of `<container>.group-<suffix>`, or None for any other id."""
    _, mark, suffix = bead_id.rpartition(GROUP_MARK)
    return suffix if mark and suffix in GROUP_SUFFIXES else None


def _plan(path: Path) -> Result[tuple[tuple[str, tuple[str, ...]], ...]]:
    """The plan file's rows: one JSON object per line, `{"sprint": "<name>", "depends_on": ["<name>", ...]}`."""
    text = _read(path, f"commit the plan file {path} named by the phase file")
    if isinstance(text, Err):
        return text
    rows: list[tuple[str, tuple[str, ...]]] = []
    fix = 'each line is {"sprint": "<name>"} with an optional "depends_on" list of sprint names'
    for number, line in enumerate(text.value.splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path}:{number}"
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            return Err(ParseFailed("parse_failed", where, str(error), fix))
        if not isinstance(value, dict) or "sprint" not in value or set(value) - PLAN_KEYS:
            return Err(ParseFailed("parse_failed", where, "not a plan row", fix))
        sprint, depends_on = value["sprint"], value.get("depends_on", [])
        if not isinstance(sprint, str) or not SPRINT_NAME.fullmatch(sprint):
            return Err(ParseFailed("parse_failed", where, f"sprint {sprint!r} is not a lowercase name", fix))
        if not isinstance(depends_on, list) or not all(isinstance(item, str) and item for item in depends_on):
            return Err(ParseFailed("parse_failed", where, f"{sprint}: depends_on is not a list of names", fix))
        if any(sprint == name for name, _ in rows):
            return Err(ParseFailed("parse_failed", where, f"sprint {sprint} is listed twice", fix))
        rows.append((sprint, tuple(depends_on)))
    if not rows:
        return Err(ParseFailed("parse_failed", str(path), "the plan lists no sprints", fix))
    names = {name for name, _ in rows}
    for name, depends_on in rows:
        unknown = [item for item in depends_on if item not in names]
        if unknown:
            message = f"{name} depends_on unknown sprint(s): {', '.join(unknown)}"
            return Err(ParseFailed("parse_failed", str(path), message, "list every depended-on sprint in the plan"))
    return Ok(tuple(rows))


def phase_file(repo_root: str, x: str) -> Result[PhaseFile]:
    """`.atm-bd/phase-<x>.toml` (plan, root, integration_branch) and the plan file it names."""
    if not PHASE_ID.fullmatch(x):
        return Err(ConfigInvalid("config_invalid", "phase", x, "name the phase as lowercase a-z0-9, e.g. bh"))
    path = Path(repo_root) / PHASE_DIR / f"phase-{x}.toml"
    text = _read(path, f"commit {PHASE_DIR}/phase-{x}.toml with the plan, or pass the right phase")
    if isinstance(text, Err):
        return text
    try:
        raw = tomllib.loads(text.value)
    except tomllib.TOMLDecodeError as error:
        return Err(ParseFailed("parse_failed", str(path), str(error), f"repair the TOML in {path}"))
    for key in PHASE_KEYS:
        if key not in raw:
            return Err(NotFound("not_found", f"{path}: key {key}", f"add {key} = \"...\" to {path}"))
        if not isinstance(raw[key], str) or not raw[key]:
            fix = f"set {key} in {path} to a nonempty string"
            return Err(ConfigInvalid("config_invalid", key, repr(raw[key]), fix))
    root_id = raw["root"]
    if not root_id.endswith(f"-phase-{x}") or root_id == f"-phase-{x}":
        return Err(ConfigInvalid("config_invalid", "root", root_id, f"set root in {path} to <prefix>-phase-{x}"))
    rows = _plan(Path(repo_root) / raw["plan"])
    if isinstance(rows, Err):
        return rows
    ids = {name: sprint_bead_id(root_id, x, name) for name, _ in rows.value}
    depends_on = {ids[name]: tuple(ids[item] for item in items) for name, items in rows.value}
    return Ok(PhaseFile(x, root_id, _phase_label(x), tuple(ids.values()), depends_on))


def _yaml_scalars(text: str) -> dict[str, Any]:
    """Top-level `key: value` pairs of the installer-rendered file; values are JSON (quoted strings, flow lists),
    a bare string, or an indented `- item` block list."""
    values: dict[str, Any] = {}
    block: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0] in " \t" or stripped.startswith("- "):
            if block is not None and stripped.startswith("- "):
                values[block].append(_yaml_value(stripped[2:].strip()))
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        block = key.strip() if not value else None
        values[key.strip()] = [] if not value else _yaml_value(value)
    return values


def _yaml_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip("'")


def repo_config(repo_root: str) -> Result[RepoConfig]:
    """`.claude/project/atm-bd-orchestration.yaml`; a missing key is Err(NotFound) naming it.
    primary_checkout is `repo_root`, never read from the file (plan-owner ruling on atm-bh-4)."""
    path = Path(repo_root) / REPO_CONFIG_PATH
    text = _read(path, "install atm-bd-orchestration into this repository")
    if isinstance(text, Err):
        return text
    values = _yaml_scalars(text.value)
    fields: dict[str, Any] = {"primary_checkout": repo_root}
    for field, key in REPO_KEYS:
        if key not in values:
            return Err(NotFound("not_found", f"{path}: key {key}", "re-run the atm-bd-orchestration installer"))
        value = values[key]
        if key == "reviewers_round1":
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                return Err(ConfigInvalid("config_invalid", key, repr(value), f"set {key} in {path} to a list of names"))
            value = tuple(value)
        elif not isinstance(value, str) or not value:
            return Err(ConfigInvalid("config_invalid", key, repr(value), f"set {key} in {path} to a nonempty string"))
        fields[field] = value
    return Ok(RepoConfig(**fields))


def _top_epic(bead: Bead, parents: Mapping[str, Bead]) -> str | None:
    """The root of `bead`'s parent chain within `parents`; None when the chain is broken anywhere,
    a referenced parent missing from `parents`, never the partial top."""
    top = None
    parent_id = bead.parent
    while parent_id:
        parent = parents.get(parent_id)
        if parent is None:
            return None
        top = parent.id
        parent_id = parent.parent
    return top


def _kind(bead: Bead) -> StepKind:
    stage = next((label.removeprefix(STAGE_PREFIX) for label in bead.labels if label.startswith(STAGE_PREFIX)), None)
    if stage is None:
        suffix = group_suffix(bead.id)
        stage = SUFFIX_STAGES[suffix] if suffix else None
    return STAGE_KINDS.get(stage, "other") if stage else "other"


def step(bead: Bead, parents: Mapping[str, Bead]) -> Result[Step]:
    metadata = bead.metadata
    kind = _kind(bead)
    checked = metadata.get(CHECKED_KEYS[kind]) if kind in CHECKED_KEYS else None
    if kind in CHECKED_KEYS and not checked:
        return _missing(f"{bead.id}.metadata.{CHECKED_KEYS[kind]}")
    if kind == "qa" and metadata.get(ROUND_KEY) is None:
        return _missing(f"{bead.id}.metadata.{ROUND_KEY}")
    checked_bead = parents.get(checked) if checked else None
    sprint_bead = metadata.get(SPRINT_BEAD_KEY)
    if kind == "qa" and not sprint_bead and checked_bead is not None:
        sprint_bead = checked_bead.metadata.get(SPRINT_BEAD_KEY)
    if kind == "qa" and not sprint_bead:
        return _missing(f"{bead.id}.metadata.{SPRINT_BEAD_KEY}")
    # Fix verification: a QA of a fix or finding bead carries it forward (never read from metadata).
    verifies = kind == "qa" and checked_bead is not None and (
        FIX_LABEL in checked_bead.labels or FINDING_LABEL in checked_bead.labels)
    carry = checked if verifies else None
    qa_round = metadata.get(ROUND_KEY)
    if verifies and FIX_LABEL in checked_bead.labels:
        # Fix verification is the round after the fix bead's own round (bh-4 D5).
        if checked_bead.metadata.get(ROUND_KEY) is None:
            return _missing(f"{checked_bead.id}.metadata.{ROUND_KEY}")
        qa_round = checked_bead.metadata[ROUND_KEY] + 1
    top = _top_epic(bead, parents)
    prefix = (top or bead.id).split("-", 1)[0]
    return Ok(Step(
        bead.id, metadata.get(SPRINT_KEY), kind, bool(metadata.get(QUICK_FIX_KEY)), ANCHOR_LABEL in bead.labels,
        bead.status, False, bead.assignee, metadata.get(BRANCH_KEY), metadata.get(PR_TARGET_KEY),
        metadata.get(WORKTREE_KEY), metadata.get(LAYER_KEY), metadata.get(DIFFICULTY_KEY),
        short_id(top or bead.id, prefix), top, bead.claimed_at, bead.closed_at, bead.title,
        checked, sprint_bead, qa_round, carry,
    ))


def closed_show_ids(file: PhaseFile, known: tuple[Bead, ...]) -> tuple[str, ...]:
    """The plan-derived sprint containers and group beads not among `known` (the open and ready beads):
    the ids for the one batched `show` that brings in closed sprints, never a closed sweep."""
    seen = {bead.id for bead in known}
    ids = (bead_id for sprint_id in file.sprints for bead_id in (sprint_id, *group_ids(sprint_id)))
    return tuple(bead_id for bead_id in ids if bead_id not in seen)


def _finding_of(bead: Bead, sprint_id: str) -> bool:
    if FINDING_LABEL not in bead.labels:
        return False
    owner = bead.metadata.get(SPRINT_BEAD_KEY)
    return owner == sprint_id if owner else bead.parent in (sprint_id, *group_ids(sprint_id))


def _sprint(file: PhaseFile, bead: Bead, beads: tuple[Bead, ...]) -> Result[Sprint]:
    meta = bead.metadata
    for key in SPRINT_META:
        if meta.get(key) in (None, ""):
            fix = f"set metadata.{key} on sprint bead {bead.id}"
            return Err(NotFound("not_found", f"{bead.id}.metadata.{key}", fix))
    if not isinstance(meta["layer"], int) or isinstance(meta["layer"], bool):
        layer = repr(meta["layer"])
        return Err(ConfigInvalid("config_invalid", f"{bead.id}.metadata.layer", layer, "set an integer layer"))
    findings = sum(1 for item in beads if _finding_of(item, bead.id))
    return Ok(Sprint(bead.id, bead.title, meta["layer"], meta["branch"], meta["pr_target"], meta["stack"],
                     file.depends_on.get(bead.id, ()), bead.status == "closed", findings))


def phase(
    file: PhaseFile, root: Bead | None, open_beads: tuple[Bead, ...], ready: tuple[Bead, ...], shown: tuple[Bead, ...],
) -> Result[Phase]:
    """Err only when the root is unusable; a plan sprint that is absent or malformed is in `step_errors`."""
    if root is None or root.id != file.root_id:
        found = "no root bead" if root is None else f"root bead {root.id}"
        fix = f"bd show {file.root_id}; fix root in the phase file"
        return Err(NotFound("not_found", f"{file.root_id} ({found})", fix))
    if file.label not in root.labels:
        return Err(ConfigInvalid("config_invalid", f"{root.id}.labels", ",".join(root.labels),
                                 f"label the phase root {file.label}"))
    by_id = {bead.id: bead for bead in shown + open_beads + ready}
    beads = tuple(by_id.values())
    ready_ids = {bead.id for bead in ready}
    sprints: list[Sprint] = []
    errors: list[tuple[str, Error]] = []
    for sprint_id in file.sprints:
        bead = by_id.get(sprint_id)
        if bead is None:
            fix = "show closed_show_ids() in the batched bd show, or pour the sprint container"
            errors.append((sprint_id, NotFound("not_found", f"sprint bead {sprint_id} listed in the plan", fix)))
            continue
        mapped = _sprint(file, bead, beads)
        if isinstance(mapped, Err):
            errors.append((sprint_id, mapped.error))
        else:
            sprints.append(mapped.value)
    steps: list[Step] = []
    for bead in beads:
        if bead.id in file.sprints or bead.id == root.id:
            continue
        mapped_step = step(bead, by_id)
        if isinstance(mapped_step, Err):
            errors.append((bead.id, mapped_step.error))
        else:
            steps.append(replace(mapped_step.value, ready=bead.id in ready_ids))
    closed = root.status == "closed"
    return Ok(Phase(root.id, file.label, root.title, closed, tuple(sprints), tuple(steps), tuple(errors)))


def dispatch_spec(ctx: DispatchContext) -> Result[DispatchSpec]:
    current, pr = ctx.step, ctx.pr
    template = TEMPLATES.get(current.kind)
    if template is None:
        return Err(ConfigInvalid("config_invalid", f"{current.bead_id}.kind", current.kind,
                                 f"dispatch only {' or '.join(TEMPLATES)} steps"))
    # Template vars are JSON: layer PRs go as their numbers, the form `gh pr view <n>` takes.
    layer_prs = [layer.number for layer in ctx.layer_prs]
    values = {
        "task_id": current.bead_id, "checked_bead": current.checked_bead or "", "sprint": current.sprint or "",
        "worktree_path": ctx.worktree_path, "branch": current.branch or "", "commit": ctx.commit, "base": ctx.base,
        "pr_number": str(pr.number) if pr else "", "pr_url": pr.url if pr else "", "layer_prs": layer_prs,
    }
    if current.kind == "sanity":
        values |= {"primary_checkout": ctx.repo.primary_checkout, "lint_command": ctx.repo.lint_command}
        return Ok(DispatchSpec(template, values))
    if current.qa_round is None:
        return _missing(f"{current.bead_id}.metadata.{ROUND_KEY}")
    values |= {
        "phase": ctx.phase_id, "phase_feature": ctx.phase_title, "stack": ctx.sprint.stack, "layer": ctx.sprint.layer,
        "round": current.qa_round, "policy_path": ctx.repo.policy_path,
        "reviewers_round1": list(ctx.repo.reviewers_round1), "sprint_bead": current.sprint_bead or "",
    }
    if current.carry_forward:
        values["carry_forward"] = current.carry_forward
    return Ok(DispatchSpec(template, values))
