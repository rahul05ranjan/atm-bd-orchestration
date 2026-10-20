from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .enums import SprintState
from .ids import short_id
from .result import ConfigInvalid, Err, Error, IoFailed, NotFound, Ok, ParseFailed, Result
from .types import Bead, DispatchContext, DispatchSpec, Phase, PhaseFile, RepoConfig, Sprint, Step, StepKind

# atm-bd conventions (atm-beads sprint_index_common.py): the only place they are named.
PHASE_DIR = ".atm-bd"
PHASE_KEYS = ("plan", "root", "integration_branch")
TEMPLATE_PURPOSES = ("dev", "fix", "sanity", "qa", "plan_review", "review")
RETIRED_TEMPLATE_PURPOSES = frozenset({"dev_fix"})
TEMPLATE_DIRECTORY = Path(".claude/skills/atm-bd-orchestration/templates")
PLAN_KEYS = frozenset({"sprint", "depends_on"})
PHASE_ID = re.compile(r"[a-z0-9]+")
SPRINT_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
GROUP_MARK = ".group-"
GROUP_SUFFIXES = ("dev", "sanity", "qa")
SPRINT_META = ("layer", "branch", "pr_target", "stack")
STAGE_PREFIX = "stage:"
FINDING_LABEL = f"{STAGE_PREFIX}finding"
FIX_LABEL = f"{STAGE_PREFIX}fix"
SEVERITY_PREFIX = "severity:"
ANCHOR_LABEL = "anchor"
SANITY_KIND: StepKind = "sanity"
QA_KIND: StepKind = "qa"
OTHER_KIND: StepKind = "other"
# stage:<stage> label -> StepKind; a bead without a stage label takes the stage of its group suffix.
STAGE_KINDS: Mapping[str, StepKind] = {
    "dev": "dev", "fix": "fix", "finding": "finding", "dev-sanity": SANITY_KIND, "qa": QA_KIND,
    "plan-review": "plan_review", "sprint": "container",
}
SUFFIX_STAGES: dict[str, str] = dict(zip(GROUP_SUFFIXES, ("dev", "dev-sanity", "qa"), strict=True))
# Step metadata keys (formulas sprint-group/finding-group, qa-bead.json.j2, finding-bead.json.j2).
CHECKED_KEYS: Mapping[StepKind, str] = {SANITY_KIND: "dev_bead", QA_KIND: "checked_bead"}
ROUND_KEY = "round"
SPRINT_KEY = "sprint"
SPRINT_BEAD_KEY = "sprint_bead"
SEVERITY_KEY = "severity"
QUICK_FIX_KEY = "quick_fix"
BRANCH_KEY, PR_TARGET_KEY, WORKTREE_KEY, LAYER_KEY, DIFFICULTY_KEY = "branch", "pr_target", "worktree", "layer", "difficulty"
# Semantic source name -> production template variable. Dispatch code uses these names
# so the template contract is defined here once, next to the template paths.
DISPATCH_KEYS: Mapping[str, str] = {
    "task": "task_id", "checked": "checked_bead", "sprint": "sprint",
    "worktree": "worktree_path", "branch": "branch", "commit": "commit", "base": "base",
    "pr_number": "pr_number", "pr_url": "pr_url", "layer_prs": "layer_prs",
    "primary_checkout": "primary_checkout", "lint_command": "lint_command",
    "phase": "phase", "phase_title": "phase_feature", "stack": "stack", "layer": "layer",
    "round": "round", "policy": "policy_path", "reviewers": "reviewers_round1",
    "sprint_bead": "sprint_bead", "carry_forward": "carry_forward",
}
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


def _phase_templates(root: Path, raw: object) -> Result[Mapping[str, str]]:
    templates = raw if isinstance(raw, dict) else {}
    for purpose in sorted(templates.keys() - TEMPLATE_PURPOSES - RETIRED_TEMPLATE_PURPOSES):
        key = f"templates.{purpose}"
        return Err(ConfigInvalid("config_invalid", key, repr(templates[purpose]), f"remove unknown {key}; use {', '.join(TEMPLATE_PURPOSES)}"))
    for purpose in TEMPLATE_PURPOSES:
        key = f"templates.{purpose}"
        value = templates.get(purpose)
        fix = f"set {key} to an existing repository-relative file under {TEMPLATE_DIRECTORY}"
        if not isinstance(value, str) or not value.strip():
            return Err(ConfigInvalid("config_invalid", key, repr(value), fix))
        path = Path(value)
        try:
            valid = (
                not value.endswith("/") and not path.is_absolute()
                and ".." not in path.parts and path.is_relative_to(TEMPLATE_DIRECTORY)
                and (root / path).resolve().is_relative_to((root / TEMPLATE_DIRECTORY).resolve())
                and os.path.isfile(os.path.join(root, value))
            )
        except (OSError, RuntimeError, ValueError):
            valid = False
        if not valid:
            return Err(ConfigInvalid("config_invalid", key, repr(value), fix))
    return Ok({purpose: templates[purpose] for purpose in TEMPLATE_PURPOSES})


def phase_file(repo_root: str, x: str) -> Result[PhaseFile]:
    """Read the phase identity, dispatch settings, and its named plan file."""
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
    lead = raw.get("lead")
    if not isinstance(lead, str) or not lead.strip():
        return Err(ConfigInvalid("config_invalid", "lead", repr(lead), f"set lead in {path} to a nonempty ATM identity"))
    code_checkout = raw.get("code_checkout")
    if code_checkout is not None and (not isinstance(code_checkout, str) or not code_checkout.strip()
                                      or Path(code_checkout).is_absolute()):
        return Err(ConfigInvalid("config_invalid", "code_checkout", repr(code_checkout),
                                 f"set code_checkout in {path} to a repository-relative path of the code repository"))
    templates = _phase_templates(Path(repo_root), raw.get("templates"))
    if isinstance(templates, Err):
        return templates
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
    return Ok(PhaseFile(x, root_id, _phase_label(x), tuple(ids.values()), depends_on, lead=lead,
                        templates=templates.value, code_checkout=code_checkout))


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
    return STAGE_KINDS.get(stage, OTHER_KIND) if stage else OTHER_KIND


def _qa_sprint_bead(bead: Bead, checked_id: str | None, parents: Mapping[str, Bead]) -> str | None:
    """Resolve legacy QA beads whose creator omitted sprint_bead metadata."""
    sprint_bead = bead.metadata.get(SPRINT_BEAD_KEY)
    if sprint_bead:
        return sprint_bead
    checked = parents.get(checked_id) if checked_id else None
    if checked is not None and checked.metadata.get(SPRINT_BEAD_KEY):
        return checked.metadata[SPRINT_BEAD_KEY]
    parent = parents.get(bead.parent) if bead.parent else None
    if parent is not None and _kind(parent) == "container":
        return parent.id
    sprint = bead.metadata.get(SPRINT_KEY)
    matches = tuple(
        item.id for item in parents.values()
        if _kind(item) == "container" and item.metadata.get(SPRINT_KEY) == sprint
    ) if sprint else ()
    return matches[0] if len(matches) == 1 else None


def _severity(bead: Bead) -> str | None:
    """A finding or fix bead's severity: its metadata, else its `severity:<value>` label."""
    value = bead.metadata.get(SEVERITY_KEY)
    if isinstance(value, str) and value:
        return value
    return next((label.removeprefix(SEVERITY_PREFIX) for label in bead.labels if label.startswith(SEVERITY_PREFIX)), None)


def step(bead: Bead, parents: Mapping[str, Bead]) -> Result[Step]:
    metadata = bead.metadata
    kind = _kind(bead)
    checked = metadata.get(CHECKED_KEYS[kind]) if kind in CHECKED_KEYS else None
    if kind in CHECKED_KEYS and not checked:
        return _missing(f"{bead.id}.metadata.{CHECKED_KEYS[kind]}")
    if kind == QA_KIND and metadata.get(ROUND_KEY) is None:
        return _missing(f"{bead.id}.metadata.{ROUND_KEY}")
    checked_bead = parents.get(checked) if checked else None
    sprint_bead = _qa_sprint_bead(bead, checked, parents) if kind == QA_KIND else metadata.get(SPRINT_BEAD_KEY)
    if kind == QA_KIND and not sprint_bead:
        return _missing(f"{bead.id}: neither metadata.{SPRINT_BEAD_KEY} nor metadata.{SPRINT_KEY}")
    # Fix verification: a QA of a fix or finding bead carries it forward (never read from metadata).
    verifies = kind == QA_KIND and checked_bead is not None and (
        FIX_LABEL in checked_bead.labels or FINDING_LABEL in checked_bead.labels)
    carry = checked if verifies else None
    qa_round = metadata.get(ROUND_KEY)
    if verifies and checked_bead is not None and FIX_LABEL in checked_bead.labels:
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
        checked, sprint_bead, qa_round, carry, bead.close_reason, _severity(bead),
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
                     file.depends_on.get(bead.id, ()), bead.status == SprintState.CLOSED, findings, bead.status,
                     bead.raw_status or str(bead.status)))


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
    errors: list[tuple[str, Error]] = []
    ready_ids = {bead.id for bead in ready}
    sprints: list[Sprint] = []
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
    closed = root.status == SprintState.CLOSED
    return Ok(Phase(root.id, file.label, root.title, closed, tuple(sprints), tuple(steps), tuple(errors)))


def dispatch_spec(ctx: DispatchContext, templates: Mapping[str, str]) -> Result[DispatchSpec]:
    current, pr = ctx.step, ctx.pr
    if current.kind not in (SANITY_KIND, QA_KIND):
        return Err(ConfigInvalid("config_invalid", f"{current.bead_id}.kind", current.kind,
                                 f"dispatch only {SANITY_KIND} or {QA_KIND} steps"))
    template = templates.get(current.kind)
    if not isinstance(template, str) or not template.strip():
        key = f"templates.{current.kind}"
        return Err(ConfigInvalid("config_invalid", key, repr(template), f"set {key} in the phase TOML"))
    # Template vars are JSON: layer PRs go as their numbers, the form `gh pr view <n>` takes.
    layer_prs = [layer.number for layer in ctx.layer_prs]
    keys = DISPATCH_KEYS
    values = {
        keys["task"]: current.bead_id, keys["checked"]: current.checked_bead or "",
        keys["sprint"]: current.sprint or "", keys["worktree"]: ctx.worktree_path,
        keys["branch"]: current.branch or "", keys["commit"]: ctx.commit, keys["base"]: ctx.base,
        keys["pr_number"]: str(pr.number) if pr else "", keys["pr_url"]: pr.url if pr else "",
        keys["layer_prs"]: layer_prs,
    }
    if current.kind == SANITY_KIND and template.rsplit("/", 1)[-1] == "dev-sanity-template.xml.j2":
        return Err(ConfigInvalid(
            "config_invalid", f"{current.bead_id}.sanity_assignment", "close vars unavailable",
            "supply beads_dir, phase_lead, and close_context from the dev close handoff before dispatching sanity",
        ))
    if current.kind == SANITY_KIND:
        values |= {
            keys["primary_checkout"]: ctx.repo.primary_checkout,
            keys["lint_command"]: ctx.repo.lint_command,
        }
        return Ok(DispatchSpec(template, values))
    if current.qa_round is None:
        return _missing(f"{current.bead_id}.metadata.{ROUND_KEY}")
    values |= {
        keys["phase"]: ctx.phase_id, keys["phase_title"]: ctx.phase_title,
        keys["stack"]: ctx.sprint.stack, keys["layer"]: ctx.sprint.layer,
        keys["round"]: current.qa_round, keys["policy"]: ctx.repo.policy_path,
        keys["reviewers"]: list(ctx.repo.reviewers_round1),
        keys["sprint_bead"]: current.sprint_bead or "",
    }
    if current.carry_forward:
        values[keys["carry_forward"]] = current.carry_forward
    return Ok(DispatchSpec(template, values))
