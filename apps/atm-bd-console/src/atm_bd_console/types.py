from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypeAlias

from .result import Error, Result

Presence: TypeAlias = Literal["active", "idle", "offline", "blocked", "unknown"]
StepKind: TypeAlias = Literal["dev", "fix", "sanity", "qa", "finding", "plan_review", "container", "other"]
AgentStatus: TypeAlias = Literal["busy", "stalling", "blocked", "offline", "idle"]


@dataclass(frozen=True)
class Member: name: str; presence: Presence; presence_since: datetime
@dataclass(frozen=True)
class Task: id: str; agent: str; state: Literal["assigned", "active"]; position: int; assigned_at: datetime; state_since: datetime; description: str
@dataclass(frozen=True)
class Bead: id: str; title: str; status: Literal["open", "in_progress", "blocked", "closed"]; labels: tuple[str, ...]; parent: str | None; blocked_by: tuple[str, ...]; assignee: str | None; metadata: Mapping[str, Any]; created_at: datetime; claimed_at: datetime | None; closed_at: datetime | None
@dataclass(frozen=True)
class Step: bead_id: str; sprint: str | None; kind: StepKind; quick_fix: bool; anchor: bool; status: Literal["open", "in_progress", "blocked", "closed"]; ready: bool; assignee: str | None; branch: str | None; target: str | None; worktree: str | None; layer: int | None; difficulty: Literal["hard", "normal", "fast"] | None; tag: str; top_epic: str | None; claimed_at: datetime | None; closed_at: datetime | None; title: str; checked_bead: str | None; sprint_bead: str | None; qa_round: int | None; carry_forward: str | None
@dataclass(frozen=True)
class Sprint: id: str; title: str; layer: int; branch: str; target: str; depends_on: tuple[str, ...]; closed: bool; findings: int
@dataclass(frozen=True)
class Phase: root_id: str; label: str; title: str; closed: bool; sprints: tuple[Sprint, ...]; steps: tuple[Step, ...]; step_errors: tuple[tuple[str, Error], ...]
@dataclass(frozen=True)
class DispatchSpec: template: str; vars: Mapping[str, Any]
@dataclass(frozen=True)
class RemoteRef: branch: str; sha: str
@dataclass(frozen=True)
class Merge: branch: str; target: str; state: Literal["clean", "conflict", "error"]; files: tuple[str, ...]; messages: tuple[str, ...]; checked_at: datetime
@dataclass(frozen=True)
class Worktree: path: str; branch: str | None; head: str
@dataclass(frozen=True)
class PullRequest: number: int; url: str; head: str; base: str; head_sha: str; ci: Literal["success", "pending", "failure", "none"]; failed_checks: tuple[str, ...]
@dataclass(frozen=True)
class GhStatus: mode: Literal["graphql", "rest", "off"]; ok: bool | None; error: Error | None; at: datetime | None; truncated: bool
@dataclass(frozen=True)
class RepoConfig: bead_prefix: str; sanity_member: str; qa_member: str; primary_checkout: str; worktree_base: str; lint_command: str; policy_path: str; reviewers_round1: tuple[str, ...]; plans_dir: str
@dataclass(frozen=True)
class Config: phase: str; lead: str; github: Literal["graphql", "rest", "off"]; interval_s: int; dry_run: bool; no_fetch: bool; repo_root: str; state_dir: str
@dataclass(frozen=True)
class DispatchContext: step: Step; sprint: Sprint; phase_id: str; phase_title: str; stack: str; lead: str; commit: str; base: str; worktree_path: str; pr: PullRequest | None; layer_prs: tuple[PullRequest, ...]; repo: RepoConfig
@dataclass(frozen=True)
class Inputs: now: datetime; lead: str; phase: Result[Phase]; members: Result[tuple[Member, ...]]; tasks: Result[tuple[Task, ...]]; worktrees: Result[tuple[Worktree, ...]]; remotes: Result[tuple[RemoteRef, ...]] | None; merges: tuple[Result[Merge], ...] | None; prs: Result[tuple[PullRequest, ...]] | None; gh: GhStatus
@dataclass(frozen=True)
class Row: task_id: str; state: Literal["running", "queued"]; kind: Literal["dev", "fix", "sprint_qa", "fix_qa", "other"]; difficulty: Literal["hard", "normal", "fast"] | None; tag: str; marker: Literal["red", "yellow"] | None; age_s: int; description: str
@dataclass(frozen=True)
class AgentView: name: str; status: AgentStatus; status_since: datetime; depth: int; rows: tuple[Row, ...]
@dataclass(frozen=True)
class SectionRow: bead_id: str; age_s: int; reason: str; pr: int | None
@dataclass(frozen=True)
class Section: label: str; rows: tuple[SectionRow, ...]
@dataclass(frozen=True)
class StepView: bead_id: str; kind: StepKind; status: str; assignee: str | None
@dataclass(frozen=True)
class SprintView: id: str; title: str; state: Literal["closed", "in_progress", "ready", "not_ready"]; depends_on: tuple[str, ...]; steps: tuple[StepView, ...]
@dataclass(frozen=True)
class Problem: key: str; bead_id: str | None; to: str; text: str; first_seen: datetime
@dataclass(frozen=True)
class Action: at: datetime; kind: Literal["assign", "send", "batch"]; to: str; bead_ids: tuple[str, ...]; text: str; spec: DispatchSpec | None
@dataclass(frozen=True)
class PrRow: pr: PullRequest; layer: int | None; bead_id: str | None; top: bool; merge: Merge | None
@dataclass(frozen=True)
class Metrics: done: int; total: int; pct: int; active_s: int
@dataclass(frozen=True)
class Due: fetch: bool; merges: tuple[tuple[str, str], ...]; github: bool
@dataclass(frozen=True)
class Snapshot: schema: int; generated_at: datetime; phase: str; root_closed: bool; stopped: Literal["closed", "idle"] | None; agents: tuple[AgentView, ...]; sections: tuple[Section, ...]; problems: tuple[Problem, ...]; errors: tuple[Error, ...]; metrics: Metrics; gh: GhStatus; top_pr: int | None; top_ci: str | None; mermaid: str; sprints: tuple[SprintView, ...]; interval_s: int; lead: str; bead_prefix: str; sent: tuple[Action, ...]; prs: tuple[PrRow, ...]; files: Mapping[str, str]
@dataclass(frozen=True)
class State: problems: tuple[Problem, ...]; cycle_first_send: datetime | None; last_batch: datetime | None; last_cycle: datetime | None; idle_since: datetime | None; sent: tuple[Action, ...]; remotes: tuple[RemoteRef, ...]; batch_due: Mapping[str, datetime]
@dataclass(frozen=True)
class PhaseFile: phase: str; root_id: str; label: str; sprints: tuple[str, ...]; depends_on: Mapping[str, tuple[str, ...]]
@dataclass(frozen=True)
class MergeFile: checked_at: datetime; merges: Mapping[str, Merge]
@dataclass(frozen=True)
class PrsFile: read_at: datetime; gh: GhStatus; prs: tuple[PullRequest, ...]
@dataclass(frozen=True)
class ActionLine: at: datetime; kind: Literal["assign", "send", "batch", "hold", "dry_run"]; to: str | None; bead: str | None; text: str

SpecFn: TypeAlias = Callable[[DispatchContext], Result[DispatchSpec]]
StepFn: TypeAlias = Callable[[Config, State, bool], tuple[Snapshot, State]]
