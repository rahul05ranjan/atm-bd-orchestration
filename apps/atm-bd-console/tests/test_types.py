from __future__ import annotations

import ast
import re
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

from atm_bd_console import result, types
from atm_bd_console.enums import QaState, SprintState

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "atm_bd_console"

# Field names in declaration order, from the bh-1 design contract plus its later contract layers:
# Sprint.stack and Inputs.repo/interval_s (d7ec505), DispatchContext.stack removed (Sprint.stack is its one owner),
# Inputs.task_beads (bh-6 f4: separates a task with no bead from one whose bead is outside the phase),
# Inputs.closed_tasks (#491: candidates whose ATM task is closed, read with `atm task events`).
FIELDS: dict[str, str] = {
    "Ok": "value",
    "Err": "error",
    "CommandFailed": "kind argv code stderr fix",
    "Timeout": "kind argv seconds fix",
    "Rejected": "kind argv status message fix",
    "Unreachable": "kind argv message fix",
    "ParseFailed": "kind source message fix",
    "NotFound": "kind what fix",
    "ConfigInvalid": "kind key value fix",
    "LockHeld": "kind snapshot_age_s fix",
    "IoFailed": "kind path message fix",
    "Member": "name presence presence_since agent_type model harness",
    "Task": "id agent state position assigned_at state_since description",
    "Bead": "id title status labels parent blocked_by assignee metadata created_at claimed_at closed_at close_reason raw_status",
    "Step": "bead_id sprint kind quick_fix anchor status ready assignee branch target worktree layer difficulty tag "
            "top_epic claimed_at closed_at title checked_bead sprint_bead qa_round carry_forward close_reason severity",
    "Sprint": "id title layer branch target stack depends_on closed findings status raw_status",
    "Phase": "root_id label title closed sprints steps step_errors",
    "DispatchSpec": "template vars",
    "RemoteRef": "branch sha",
    "Merge": "branch target state files messages checked_at",
    "Worktree": "path branch head",
    "PullRequest": "number url head base head_sha ci failed_checks state raw_ci raw_state",
    "GhStatus": "mode ok error at truncated quota_remaining quota_limit quota_reset paused rate_limited poll_interval_s quota_threshold core_remaining core_limit core_reset graphql_remaining graphql_limit graphql_reset quota_at",
    "RepoConfig": "bead_prefix sanity_member qa_member primary_checkout worktree_base lint_command policy_path "
                  "reviewers_round1 plans_dir",
    "Config": "phase lead team github interval_s dry_run no_fetch repo_root state_dir notification_sender github_poll_interval_s github_quota_threshold code_root github_repo",
    "DispatchContext": "step sprint phase_id phase_title lead commit base worktree_path pr layer_prs repo",
    "Inputs": "now lead repo interval_s phase members tasks worktrees remotes merges prs gh task_beads closed_tasks",
    "Row": "task_id state kind difficulty tag marker age_s description",
    "AgentView": "name status status_since depth rows agent_type model harness qa_state",
    "SectionRow": "bead_id age_s reason pr",
    "Section": "label rows",
    "StepView": "bead_id kind status assignee",
    "SprintView": "id title state depends_on steps",
    "Problem": "key bead_id to text first_seen",
    "Action": "at kind to bead_ids text spec outcome",
    "PrRow": "pr layer bead_id top merge assignee",
    "Metrics": "done total pct active_s",
    "Due": "fetch merges github",
    "Snapshot": "schema generated_at phase root_closed stopped agents sections problems errors metrics gh top_pr "
                "top_ci mermaid sprints interval_s lead bead_prefix sent prs files phase_data task_data member_data task_bead_data worktree_data",
    "State": "problems cycle_first_send last_batch last_cycle idle_since sent remotes batch_due collision_notices",
    "PhaseFile": "phase root_id label sprints depends_on lead templates code_checkout",
    "MergeFile": "checked_at merges",
    "PrsFile": "read_at gh prs attempted_at",
    "ActionLine": "at kind to bead text",
}

DIFFICULTY = ("hard", "normal", "fast")
GITHUB_MODE = ("graphql", "rest", "off")
# (class, field) -> the Literal values the design names; Optional literals compare without None.
LITERALS: dict[tuple[str, str], tuple[str, ...]] = {
    ("Member", "presence"): ("active", "idle", "offline", "blocked", "unknown"),
    ("Task", "state"): ("assigned", "active"),
    ("Step", "kind"): ("dev", "fix", "sanity", "qa", "finding", "plan_review", "container", "other"),
    ("Step", "difficulty"): DIFFICULTY,
    ("GhStatus", "mode"): GITHUB_MODE,
    ("Config", "github"): GITHUB_MODE,
    ("Row", "state"): ("running", "queued"),
    ("Row", "kind"): ("dev", "fix", "sprint_qa", "fix_qa", "other"),
    ("Row", "difficulty"): DIFFICULTY,
    ("Row", "marker"): ("red", "yellow"),
    ("AgentView", "status"): ("busy", "stalling", "blocked", "offline", "idle"),
    ("StepView", "kind"): ("dev", "fix", "sanity", "qa", "finding", "plan_review", "container", "other"),
    ("Action", "kind"): ("assign", "send", "batch"),
    ("Snapshot", "stopped"): ("closed", "idle"),
    ("ActionLine", "kind"): ("assign", "send", "batch", "hold", "dry_run"),
    **{(name, "kind"): (kind,) for name, kind in (
        ("CommandFailed", "command_failed"), ("Timeout", "timeout"), ("Rejected", "rejected"),
        ("Unreachable", "unreachable"), ("ParseFailed", "parse_failed"), ("NotFound", "not_found"),
        ("ConfigInvalid", "config_invalid"), ("LockHeld", "lock_held"), ("IoFailed", "io_failed"),
    )},
}
ERROR_NAMES = ("CommandFailed", "Timeout", "Rejected", "Unreachable", "ParseFailed", "NotFound", "ConfigInvalid",
               "LockHeld", "IoFailed")


def _class(name: str) -> type:
    return getattr(result, name, None) or getattr(types, name)


def _literal_values(annotation: object) -> tuple[str, ...]:
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    literals = [arg for arg in get_args(annotation) if get_origin(arg) is Literal]
    assert len(literals) == 1, annotation
    assert type(None) in get_args(annotation), annotation
    return get_args(literals[0])


def test_every_contract_class_is_listed() -> None:
    declared = {
        name for module in (result, types) for name, value in vars(module).items()
        if isinstance(value, type) and is_dataclass(value) and value.__module__ == module.__name__
    }
    assert declared == set(FIELDS)


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_contract_class_is_frozen_with_design_fields(name: str) -> None:
    cls = _class(name)
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert tuple(field.name for field in fields(cls)) == tuple(FIELDS[name].split())


@pytest.mark.parametrize(("name", "field"), sorted(LITERALS))
def test_literal_arguments_match_design(name: str, field: str) -> None:
    assert _literal_values(get_type_hints(_class(name))[field]) == LITERALS[(name, field)]


def test_key_field_annotations() -> None:
    assert get_type_hints(types.Bead)["status"] is SprintState
    assert get_type_hints(types.Step)["status"] is SprintState
    assert get_type_hints(types.StepView)["status"] is SprintState
    assert get_type_hints(types.AgentView)["qa_state"] == QaState | None
    assert get_type_hints(types.Snapshot)["generated_at"] is datetime
    assert get_type_hints(types.Phase)["step_errors"] == tuple[tuple[str, result.Error], ...]
    assert get_type_hints(types.GhStatus)["error"] == result.Error | None
    assert get_type_hints(types.Inputs)["phase"] == result.Result[types.Phase]
    assert get_type_hints(types.Inputs)["remotes"] == result.Result[tuple[types.RemoteRef, ...]] | None
    assert get_type_hints(result.LockHeld)["snapshot_age_s"] == int | None


def test_tick_context_contract_field_names_are_pinned() -> None:
    assert tuple(field.name for field in fields(types.Sprint)) == (
        "id", "title", "layer", "branch", "target", "stack", "depends_on", "closed", "findings", "status", "raw_status",
    )
    assert tuple(field.name for field in fields(types.RepoConfig)) == (
        "bead_prefix", "sanity_member", "qa_member", "primary_checkout", "worktree_base", "lint_command",
        "policy_path", "reviewers_round1", "plans_dir",
    )
    assert tuple(field.name for field in fields(types.Inputs)) == (
        "now", "lead", "repo", "interval_s", "phase", "members", "tasks", "worktrees", "remotes", "merges",
        "prs", "gh", "task_beads", "closed_tasks",
    )


def test_type_aliases() -> None:
    assert get_args(types.Presence) == LITERALS[("Member", "presence")]
    assert get_args(types.StepKind) == LITERALS[("Step", "kind")]
    assert get_args(types.AgentStatus) == LITERALS[("AgentView", "status")]
    assert get_args(result.Error) == tuple(_class(name) for name in ERROR_NAMES)
    assert get_args(result.Result) == (result.Ok[result.T], result.Err)
    assert get_args(types.SpecFn) == ([types.DispatchContext], result.Result[types.DispatchSpec])
    args, result_type = get_args(types.StepFn)
    assert args == [types.Config, types.State, bool]
    assert result_type is types.StepResult


@pytest.mark.parametrize("module", ["result.py", "types.py"])
def test_d2_contract_modules_hold_no_logic(module: str) -> None:
    text = (PACKAGE / module).read_text()
    assert re.findall(r"\braise\b|subprocess|open\(|\bdef ", text) == []
    tree = ast.parse(text)
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Raise))]
