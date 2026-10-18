from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from rich.table import Table
from rich.text import Text

from .enums import AgentIcon, CiState, PrState, SprintState
from .facts import SprintFacts, sprint_facts
from .icons import icon
from .ids import short_id
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
    return facts.agents


def _qa(facts: SprintFacts) -> StateCell:
    return (facts.qa_result,) if facts.status == SprintState.IN_PROGRESS and facts.qa_result else ()


def _sprint(facts: SprintFacts) -> StateCell:
    return (facts.status,)


STATE: tuple[Callable[[SprintFacts], StateCell], ...] = (_agents, _qa, _sprint)


def _ci_unknown(facts: SprintFacts) -> CiState | None:
    return CiState.UNKNOWN if facts.ci == CiState.UNKNOWN else None


def _ci_none(facts: SprintFacts) -> CiState | None:
    return CiState.NONE if facts.ci == CiState.NONE else None


def _ci_platform(facts: SprintFacts) -> CiState | None:
    match facts.ci:
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
    return CiState.FAIL_BUG if facts.ci == CiState.FAIL_BUG else None


def _ci_running(facts: SprintFacts) -> CiState | None:
    return CiState.RUNNING if facts.ci == CiState.RUNNING else None


def _ci_pass(facts: SprintFacts) -> CiState | None:
    return CiState.PASS if facts.ci == CiState.PASS else None


CI: tuple[Callable[[SprintFacts], CiState | None], ...] = (
    _ci_unknown, _ci_none, _ci_platform, _ci_bug, _ci_running, _ci_pass,
)


def _pr_unknown(facts: SprintFacts) -> PrState | None:
    return PrState.UNKNOWN if facts.pr_state == PrState.UNKNOWN else None


def _pr_none(facts: SprintFacts) -> PrState | None:
    return PrState.NONE if facts.pr_state == PrState.NONE else None


def _pr_conflict(facts: SprintFacts) -> PrState | None:
    return PrState.CONFLICT if facts.pr_state == PrState.CONFLICT else None


def _pr_state(facts: SprintFacts) -> PrState | None:
    return facts.pr_state


PR: tuple[Callable[[SprintFacts], PrState | None], ...] = (_pr_unknown, _pr_none, _pr_conflict, _pr_state)


def rows(snapshot: Snapshot) -> tuple[StatusRow, ...]:
    result = []
    for facts in sprint_facts(snapshot):
        state = next(value for producer in STATE if (value := producer(facts)))
        ci = next(value for producer in CI if (value := producer(facts)) is not None)
        pr_state = next(value for producer in PR if (value := producer(facts)) is not None)
        pr = facts.pr.pr if facts.pr else None
        result.append(StatusRow(short_id(facts.sprint.id, snapshot.bead_prefix), state, ci, pr_state,
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
