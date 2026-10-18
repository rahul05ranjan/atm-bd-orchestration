from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from rich.table import Table
from rich.text import Text

from .enums import AgentIcon, CiState, PrState, SprintState
from .facts import SprintFacts, sprint_facts
from .icons import RenderTarget, icon
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


def _agents_working(facts: SprintFacts) -> StateCell:
    return tuple(agent.icon for agent in facts.active)


def _blocked_or_ready(facts: SprintFacts) -> StateCell:
    return (facts.status,) if facts.status in (SprintState.BLOCKED, SprintState.READY) else ()


def _qa_from_open_blocking_children(facts: SprintFacts) -> StateCell:
    return (facts.gavel,) if facts.gavel and facts.status != SprintState.CLOSED else ()


def _sprint_status(facts: SprintFacts) -> StateCell:
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


STATE: tuple[Callable[[SprintFacts], StateCell], ...] = (
    _agents_working, _blocked_or_ready, _qa_from_open_blocking_children, _sprint_status,
)


def rows(snapshot: Snapshot) -> tuple[StatusRow, ...]:
    result = []
    for facts in sprint_facts(snapshot):
        state = next(value for producer in STATE if (value := producer(facts)))
        ci = CiState.NONE if facts.ci is None else facts.ci.status
        pr_state = PrState.NONE if facts.pr is None else facts.pr.state
        pr = facts.pr.row.pr if facts.pr and facts.pr.row else None
        result.append(StatusRow(facts.slug, state, ci, pr_state,
                                pr.number if pr else None, pr.url if pr else None))
    return tuple(result)


def markdown(values: tuple[StatusRow, ...]) -> str:
    result = ["| sprint | state | ci | pr |", "|---|:-:|:-:|---|"]
    for row in values:
        sprint = row.sprint.replace("\\", "\\\\").replace("|", "\\|")
        state = "".join(icon(value, RenderTarget.MARKDOWN)[0] for value in row.state)
        ci = icon(row.ci, RenderTarget.MARKDOWN)[0]
        pr = icon(row.pr_state, RenderTarget.MARKDOWN)[0]
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
