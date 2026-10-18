from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from rich.table import Table
from rich.text import Text

from .enums import AgentIcon, CiState, PrState, SprintState
from .facts import SprintFacts, sprint_facts
from .icons import icon
from .types import Snapshot

StateCell = tuple[AgentIcon | SprintState, ...]


@dataclass(frozen=True)
class StatusRow:
    sprint: str
    state: StateCell
    ci: CiState
    pr_state: PrState
    pr_number: int | None
    pr_url: str | None


def _agents(facts: SprintFacts) -> StateCell:
    return tuple(agent.icon for agent in facts.active)


def _qa(facts: SprintFacts) -> StateCell:
    match facts.status:
        case SprintState.IN_PROGRESS:
            return (facts.last_qa,) if facts.last_qa else ()
        case (
            SprintState.OPEN
            | SprintState.BLOCKED
            | SprintState.DEFERRED
            | SprintState.CLOSED
            | SprintState.PINNED
            | SprintState.HOOKED
            | SprintState.ACCEPTED
            | SprintState.PLANNED
            | SprintState.PROPOSED
            | SprintState.READY
            | SprintState.NOT_READY
            | SprintState.UNKNOWN
        ):
            return ()
        case _ as unreachable:
            assert_never(unreachable)


def _sprint(facts: SprintFacts) -> StateCell:
    match facts.status:
        case (
            SprintState.OPEN
            | SprintState.IN_PROGRESS
            | SprintState.BLOCKED
            | SprintState.DEFERRED
            | SprintState.CLOSED
            | SprintState.PINNED
            | SprintState.HOOKED
            | SprintState.ACCEPTED
            | SprintState.PLANNED
            | SprintState.PROPOSED
            | SprintState.READY
            | SprintState.NOT_READY
            | SprintState.UNKNOWN
        ):
            return (facts.status,)
        case _ as unreachable:
            assert_never(unreachable)


STATE: tuple[Callable[[SprintFacts], StateCell], ...] = (_agents, _qa, _sprint)


def _ci_unknown(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return None
    match facts.ci.status:
        case CiState.UNKNOWN:
            return CiState.UNKNOWN
        case (
            CiState.NONE
            | CiState.RUNNING
            | CiState.PASS
            | CiState.FAIL_MACOS
            | CiState.FAIL_WINDOWS
            | CiState.FAIL_LINUX
            | CiState.FAIL_BUG
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _ci_none(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return CiState.NONE
    match facts.ci.status:
        case CiState.NONE:
            return CiState.NONE
        case (
            CiState.RUNNING
            | CiState.PASS
            | CiState.FAIL_MACOS
            | CiState.FAIL_WINDOWS
            | CiState.FAIL_LINUX
            | CiState.FAIL_BUG
            | CiState.UNKNOWN
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _ci_platform(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return None
    match facts.ci.status:
        case CiState.FAIL_MACOS:
            return CiState.FAIL_MACOS
        case CiState.FAIL_WINDOWS:
            return CiState.FAIL_WINDOWS
        case CiState.FAIL_LINUX:
            return CiState.FAIL_LINUX
        case CiState.NONE | CiState.RUNNING | CiState.PASS | CiState.FAIL_BUG | CiState.UNKNOWN:
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _ci_bug(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return None
    match facts.ci.status:
        case CiState.FAIL_BUG:
            return CiState.FAIL_BUG
        case (
            CiState.NONE
            | CiState.RUNNING
            | CiState.PASS
            | CiState.FAIL_MACOS
            | CiState.FAIL_WINDOWS
            | CiState.FAIL_LINUX
            | CiState.UNKNOWN
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _ci_running(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return None
    match facts.ci.status:
        case CiState.RUNNING:
            return CiState.RUNNING
        case (
            CiState.NONE
            | CiState.PASS
            | CiState.FAIL_MACOS
            | CiState.FAIL_WINDOWS
            | CiState.FAIL_LINUX
            | CiState.FAIL_BUG
            | CiState.UNKNOWN
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _ci_pass(facts: SprintFacts) -> CiState | None:
    if facts.ci is None:
        return None
    match facts.ci.status:
        case CiState.PASS:
            return CiState.PASS
        case (
            CiState.NONE
            | CiState.RUNNING
            | CiState.FAIL_MACOS
            | CiState.FAIL_WINDOWS
            | CiState.FAIL_LINUX
            | CiState.FAIL_BUG
            | CiState.UNKNOWN
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


CI: tuple[Callable[[SprintFacts], CiState | None], ...] = (
    _ci_unknown, _ci_none, _ci_platform, _ci_bug, _ci_running, _ci_pass,
)


def _pr_unknown(facts: SprintFacts) -> PrState | None:
    if facts.pr is None:
        return None
    match facts.pr.state:
        case PrState.UNKNOWN:
            return PrState.UNKNOWN
        case (
            PrState.NONE
            | PrState.OPEN
            | PrState.DRAFT
            | PrState.MERGED
            | PrState.CLOSED
            | PrState.CONFLICT
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _pr_none(facts: SprintFacts) -> PrState | None:
    return PrState.NONE if facts.pr is None else None


def _pr_conflict(facts: SprintFacts) -> PrState | None:
    if facts.pr is None:
        return None
    match facts.pr.state:
        case PrState.CONFLICT:
            return PrState.CONFLICT
        case (
            PrState.NONE
            | PrState.OPEN
            | PrState.DRAFT
            | PrState.MERGED
            | PrState.CLOSED
            | PrState.UNKNOWN
        ):
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _pr_state(facts: SprintFacts) -> PrState | None:
    if facts.pr is None:
        return None
    match facts.pr.state:
        case (
            PrState.NONE
            | PrState.OPEN
            | PrState.DRAFT
            | PrState.MERGED
            | PrState.CLOSED
            | PrState.CONFLICT
            | PrState.UNKNOWN
        ):
            return facts.pr.state
        case _ as unreachable:
            assert_never(unreachable)


PR: tuple[Callable[[SprintFacts], PrState | None], ...] = (_pr_unknown, _pr_none, _pr_conflict, _pr_state)


def rows(snapshot: Snapshot) -> tuple[StatusRow, ...]:
    result = []
    for facts in sprint_facts(snapshot):
        state = next(value for producer in STATE if (value := producer(facts)))
        ci = next(value for producer in CI if (value := producer(facts)) is not None)
        pr_state = next(value for producer in PR if (value := producer(facts)) is not None)
        pr = facts.pr.row.pr if facts.pr and facts.pr.row else None
        result.append(StatusRow(facts.slug, state, ci, pr_state,
                                pr.number if pr else None, pr.url if pr else None))
    return tuple(result)


def markdown(values: tuple[StatusRow, ...]) -> str:
    result = ["| sprint | state | ci | pr |", "|---|:-:|:-:|---|"]
    for row in values:
        sprint = row.sprint.replace("\\", "\\\\").replace("|", "\\|")
        state = "".join(icon(value)[0] for value in row.state)
        ci = icon(row.ci)[0]
        pr = icon(row.pr_state)[0]
        if row.pr_number is not None and row.pr_state != PrState.UNKNOWN:
            label = f"#{row.pr_number}"
            pr += f" [{label}]({row.pr_url})" if row.pr_url else f" {label}"
        result.append(f"| {sprint} | {state} | {ci} | {pr} |")
    return "\n".join(result)


def rich_table(values: tuple[StatusRow, ...]) -> Table:
    table = Table("sprint", "state", "ci", "pr")
    for row in values:
        state = Text()
        for value in row.state:
            glyph, colour = icon(value)
            state.append(glyph, style=colour)
        glyph, colour = icon(row.ci)
        ci = Text(glyph, style=colour or "")
        glyph, colour = icon(row.pr_state)
        pr = Text(glyph, style=colour or "")
        if row.pr_number is not None and row.pr_state != PrState.UNKNOWN:
            pr.append(f" #{row.pr_number}", style=f"link {row.pr_url}" if row.pr_url else None)
        table.add_row(row.sprint, state, ci, pr)
    return table
