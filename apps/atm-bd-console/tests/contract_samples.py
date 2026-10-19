"""Populated instances of every contract type: shared by the round-trip test and the fixture generator."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from atm_bd_console.enums import CiState, MergeState, SprintState
from atm_bd_console.result import (
    CommandFailed,
    ConfigInvalid,
    Err,
    IoFailed,
    LockHeld,
    NotFound,
    Ok,
    ParseFailed,
    Rejected,
    Timeout,
    Unreachable,
)
from atm_bd_console.types import (
    Action,
    ActionLine,
    AgentView,
    Bead,
    Config,
    DispatchContext,
    DispatchSpec,
    Due,
    GhStatus,
    Inputs,
    Member,
    Merge,
    MergeFile,
    Metrics,
    Phase,
    PhaseFile,
    Problem,
    PrRow,
    PrsFile,
    PullRequest,
    RemoteRef,
    RepoConfig,
    Row,
    Section,
    SectionRow,
    Snapshot,
    Sprint,
    SprintView,
    State,
    Step,
    StepView,
    Task,
    Worktree,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(minutes=7)
# An aware non-UTC timestamp: the codec writes it as Z and it decodes equal (same instant).
PLUS_TWO = datetime(2026, 10, 14, 13, 30, tzinfo=timezone(timedelta(hours=2)))

COMMAND_FAILED = CommandFailed("command_failed", ("gh", "api", "graphql"), 1, "HTTP 502", "retry the GitHub read")
TIMEOUT = Timeout("timeout", ("bd", "ready", "--json"), 20, "retry the bd read")
REJECTED = Rejected("rejected", ("gh", "api", "graphql"), 401, "bad credentials", "run gh auth status")
UNREACHABLE = Unreachable("unreachable", ("git", "fetch", "origin"), "could not resolve host", "check the network")
PARSE_FAILED = ParseFailed("parse_failed", "$.prs[0].ci", "$.prs[0].ci: expected one of", "repair the cache")
NOT_FOUND = NotFound("not_found", "atm-bh-6.metadata.checked_bead", "set checked_bead on the QA bead")
CONFIG_INVALID = ConfigInvalid("config_invalid", "github", "soap", "use graphql, rest or off")
LOCK_HELD = LockHeld("lock_held", 42, "another console is running; use show")
IO_FAILED = IoFailed("io_failed", ".atm-bd/atm-bd-console/state.json", "permission denied", "repair permissions")
ERRORS = (COMMAND_FAILED, TIMEOUT, REJECTED, UNREACHABLE, PARSE_FAILED, NOT_FOUND, CONFIG_INVALID, LOCK_HELD, IO_FAILED)

MEMBER = Member("arch-ctm", "active", EARLIER)
TASK = Task("atm-bh-6", "arch-ctm", "active", 1, EARLIER, PLUS_TWO, "implement tick rules")
BEAD = Bead(
    "atm-bh-6", "tick rules", SprintState.IN_PROGRESS, ("phase:bh", "stage:dev"), "atm-phase-bh", ("atm-bh-1",), "arch-ctm",
    {"layer": 6, "branch": "sprint/bh-6-tick-rules", "nested": {"keys": ["a", "b"]}}, EARLIER, PLUS_TWO, NOW,
    raw_status="in_progress",
)
STEP = Step(
    "atm-bh-6.qa", "bh-6", "qa", True, True, SprintState.IN_PROGRESS, True, "quality-mgr", "sprint/bh-6-tick-rules",
    "integrate/phase-bh", "/worktrees/sprint/bh-6-tick-rules", 6, "hard", "bh-6", "atm-phase-bh", EARLIER, NOW,
    "QA bh-6", "atm-bh-6.fix", "atm-bh-6", 2, "atm-bh-6.fix",
)
SPRINT = Sprint(
    "atm-bh-6", "tick rules", 6, "sprint/bh-6-tick-rules", "integrate/phase-bh", "phase-bh", ("atm-bh-1", "atm-bh-4"),
    True, 3, SprintState.CLOSED, "closed",
)
PHASE = Phase("atm-phase-bh", "phase:bh", "atm-bd-console", True, (SPRINT,), (STEP,), (("atm-bh-7.qa", NOT_FOUND),))
PHASE_TEMPLATES = {
    purpose: f".claude/skills/atm-bd-orchestration/templates/{filename}.xml.j2"
    for purpose, filename in (
        ("dev", "dev-template"), ("fix", "fix-assignment"),
        ("sanity", "dev-sanity-template"), ("qa", "qa-template"),
        ("plan_review", "plan-review-template"), ("review", "review-template"),
    )
}
DISPATCH_SPEC = DispatchSpec(PHASE_TEMPLATES["qa"], {"round": 2, "reviewers_round1": ["req-qa"], "stack": "phase-bh"})
REMOTE_REF = RemoteRef("sprint/bh-6-tick-rules", "0123456789abcdef0123456789abcdef01234567")
MERGE = Merge(
    "sprint/bh-6-tick-rules", "integrate/phase-bh", MergeState.CONFLICT, ("src/atm_bd_console/types.py",),
    ("CONFLICT (content): Merge conflict in src/atm_bd_console/types.py",), NOW,
)
WORKTREE = Worktree("/worktrees/sprint/bh-6-tick-rules", "sprint/bh-6-tick-rules", "0123456789abcdef")
PULL_REQUEST = PullRequest(
    1850, "https://github.com/randlee/atm-core/pull/1850", "sprint/bh-6-tick-rules", "integrate/phase-bh",
    "0123456789abcdef", CiState.FAIL_BUG, ("pytest", "ruff"), raw_ci="FAILURE", raw_state="OPEN",
)
GH_STATUS = GhStatus("graphql", False, REJECTED, PLUS_TWO, True, quota_remaining=2450, quota_limit=5000,
                     quota_reset=PLUS_TWO, paused=True, rate_limited=True)
REPO_CONFIG = RepoConfig(
    "atm", "dev-sanity", "quality-mgr", "/repo", "/worktrees", "just lint", ".claude/project/quality-policy.md",
    ("req-qa", "arch-qa"), "docs/plans",
)
CONFIG = Config("bh", "fenix", "atm-dev", "rest", 60, True, True, "/repo", ".atm-bd/atm-bd-console")
DISPATCH_CONTEXT = DispatchContext(
    STEP, SPRINT, "bh", "atm-bd-console", "fenix", "0123456789abcdef", "integrate/phase-bh",
    "/worktrees/sprint/bh-6-tick-rules", PULL_REQUEST, (PULL_REQUEST,), REPO_CONFIG,
)
INPUTS = Inputs(
    NOW, "fenix", REPO_CONFIG, 60, Ok(PHASE), Ok((MEMBER,)), Err(TIMEOUT), Ok((WORKTREE,)), Ok((REMOTE_REF,)),
    (Ok(MERGE), Err(UNREACHABLE)), Ok((PULL_REQUEST,)), GH_STATUS, Ok((BEAD,)),
)
ROW = Row("atm-bh-6", "running", "sprint_qa", "normal", "bh-6", "yellow", 420, "QA bh-6 round 2")
AGENT_VIEW = AgentView("arch-ctm", "busy", EARLIER, 2, (ROW,))
SECTION_ROW = SectionRow("atm-bh-6", 420, "CI failing on the top layer", 1850)
SECTION = Section("Blocked", (SECTION_ROW,))
STEP_VIEW = StepView("atm-bh-6.qa", "qa", SprintState.IN_PROGRESS, "quality-mgr")
SPRINT_VIEW = SprintView("atm-bh-6", "tick rules", SprintState.IN_PROGRESS, ("atm-bh-1",), (STEP_VIEW,))
PROBLEM = Problem("stalled:arch-ctm", "atm-bh-6", "fenix", "arch-ctm has not started atm-bh-6", EARLIER)
ACTION = Action(NOW, "assign", "quality-mgr", ("atm-bh-6.qa",), "QA bh-6 round 2", DISPATCH_SPEC, "sent")
PR_ROW = PrRow(PULL_REQUEST, 6, "atm-bh-6", True, MERGE)
METRICS = Metrics(5, 11, 45, 3600)
DUE = Due(True, (("sprint/bh-6-tick-rules", "integrate/phase-bh"),), True)
SNAPSHOT = Snapshot(
    1, NOW, "bh", True, "closed", (AGENT_VIEW,), (SECTION,), (PROBLEM,), (COMMAND_FAILED, LOCK_HELD), METRICS,
    GH_STATUS, 1850, CiState.FAIL_BUG, "graph TD\n  atm-bh-1 --> atm-bh-6", (SPRINT_VIEW,), 60, "fenix", "atm", (ACTION,),
    (PR_ROW,), {"prs": ".atm-bd/atm-bd-console/atm-phase-bh/prs.json"},
    PHASE, (TASK,), (MEMBER,), (BEAD,), (WORKTREE,),
)
STATE = State(
    (PROBLEM,), EARLIER, EARLIER, NOW, PLUS_TWO, (ACTION,), (REMOTE_REF,), {"quality-mgr": NOW, "arch-ctm": PLUS_TWO},
    ("collision-key",),
)
PHASE_FILE = PhaseFile("bh", "atm-phase-bh", "phase:bh", ("atm-bh-1", "atm-bh-6"), {"atm-bh-6": ("atm-bh-1",)}, lead="fenix", templates=PHASE_TEMPLATES)
MERGE_FILE = MergeFile(NOW, {"sprint/bh-6-tick-rules": MERGE})
PRS_FILE = PrsFile(NOW, GH_STATUS, (PULL_REQUEST,), attempted_at=NOW)
ACTION_LINE = ActionLine(NOW, "hold", "arch-ctm", "atm-bh-6", "holding: CI red on the layer below")

# One populated instance per contract dataclass; test_codec asserts this covers every class in result and types.
SAMPLES = (
    *ERRORS, MEMBER, TASK, BEAD, STEP, SPRINT, PHASE, DISPATCH_SPEC, REMOTE_REF, MERGE, WORKTREE, PULL_REQUEST,
    GH_STATUS, REPO_CONFIG, CONFIG, DISPATCH_CONTEXT, INPUTS, ROW, AGENT_VIEW, SECTION_ROW, SECTION, STEP_VIEW,
    SPRINT_VIEW, PROBLEM, ACTION, PR_ROW, METRICS, DUE, SNAPSHOT, STATE, PHASE_FILE, MERGE_FILE, PRS_FILE, ACTION_LINE,
)
