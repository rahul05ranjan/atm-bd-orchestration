from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple, assert_never

from .enums import AgentIcon, CiState, MergeState, PrState, SprintState
from .types import AgentView, Member


class RenderTarget(StrEnum):
    TUI = "tui"
    MARKDOWN = "markdown"


class Icon(NamedTuple):
    tui: str
    colour: str | None
    markdown: str


Glyph = tuple[str, str | None]


def _same(glyph: str, colour: str | None) -> Icon:
    return Icon(glyph, colour, glyph)


# Colour-only differences are indistinguishable in markdown until it has a separate symbol table.

AGENT_ICONS: dict[AgentIcon, Icon] = {
    AgentIcon.HAIKU: _same("\U000f0156", "#FF6533"),
    AgentIcon.SONNET: _same("\U000f0382", "#FF784D"),
    AgentIcon.OPUS: _same("\U000f06c4", "#FF8C66"),
    AgentIcon.FABLE: _same("\U000f0717", "#FF9F80"),
    AgentIcon.ASTRA: _same("\U000f0674", "#FFE066"),
    AgentIcon.TERRA: _same("\U000f01e7", "#2DE057"),
    AgentIcon.LUNA: _same("\U000f0f65", "#FFE066"),
    AgentIcon.SOL: _same("\U000f05a8", "#FFA60D"),
    AgentIcon.OTHER: _same("·", None),
    AgentIcon.SEVERITY: _same("\U000f01a6", "#E551BA"),
    AgentIcon.QA_RUNNING: _same("\U000f029b", "#4A5AF8"),
    AgentIcon.QA_PASS: _same("\U000f029b", "#2EA043"),
    AgentIcon.QA_FAIL: _same("\U000f029b", "#F85149"),
}
SPRINT_ICONS: dict[SprintState, Icon] = {
    SprintState.OPEN: _same("\U000f0766", "#2D81E0"),
    SprintState.BLOCKED: _same("\U000f033e", "#FA8537"),
    SprintState.IN_PROGRESS: _same("\U000f051f", "#0077FF"),
    SprintState.DEFERRED: _same("\U000f03e5", "#FFB726"),
    SprintState.CLOSED: _same("\U000f05e0", "#2DE057"),
    SprintState.PINNED: _same("\U000f0403", "#42C3AB"),
    SprintState.HOOKED: _same("\U000f06e2", "#2DE057"),
    SprintState.ACCEPTED: _same("\U000f0513", "#E551BA"),
    SprintState.PLANNED: _same("\U000f00ef", "#FFB726"),
    SprintState.PROPOSED: _same("\U000f06e8", "#FFB726"),
    SprintState.READY: _same("\U000f040d", "#0077FF"),
    SprintState.NOT_READY: _same("\U000f137c", "#D97757"),
    SprintState.UNKNOWN: _same("?", "#2D81E0"),
}
CI_ICONS: dict[CiState, Icon] = {
    CiState.PASS: _same("\U000f062c", "#2EA043"),
    CiState.FAIL_MACOS: _same("\U000f0035", "#F85149"),
    CiState.FAIL_WINDOWS: _same("\U000f05b3", "#F85149"),
    CiState.FAIL_LINUX: _same("\U000f033d", "#F85149"),
    CiState.FAIL_BUG: _same("\U000f00e4", "#F85149"),
    CiState.RUNNING: _same("\U000f0898", "#58A6FF"),
    CiState.NONE: _same("\U000f01d8", "#2D81E0"),
    CiState.UNKNOWN: _same("?", "#2D81E0"),
}
PR_ICONS: dict[PrState, Icon] = {
    PrState.OPEN: _same("\U000f04c2", "#2D81E0"),
    PrState.DRAFT: _same("\U000f0cb6", "#FFA60D"),
    PrState.MERGED: _same("\U000f062d", "#8957E5"),
    PrState.CLOSED: _same("\U000f073a", "#F85149"),
    PrState.CONFLICT: _same("\U000f062c", "#F85149"),
    PrState.NONE: _same("-", None),
    PrState.UNKNOWN: _same("?", "#2D81E0"),
}
MERGE_CLEAN: Glyph = ("\U000f062d", "#2EA043")
MERGE_ERROR: Glyph = ("\U000f062d", "#F85149")
STATUS_OK = "✅"
STATUS_FAIL = "⛔"
MARKER_RED = "❗"
MARKER_YELLOW = "⚠️"
MARKER_HARD = "💎"
MARKER_FAST = "⚡"
MARKER_QA = "🔍"
MARKER_FIX_QA = "🛠️"


def icon(member: AgentIcon | SprintState | CiState | PrState, target: RenderTarget = RenderTarget.TUI) -> Glyph:
    match member:
        case AgentIcon():
            entry = AGENT_ICONS[member]
        case SprintState():
            entry = SPRINT_ICONS[member]
        case CiState():
            entry = CI_ICONS[member]
        case PrState():
            entry = PR_ICONS[member]
        case _ as unreachable:
            assert_never(unreachable)
    match target:
        case RenderTarget.TUI:
            return entry.tui, entry.colour
        case RenderTarget.MARKDOWN:
            return entry.markdown, entry.colour
        case _ as unreachable:
            assert_never(unreachable)


def merge_icon(state: MergeState) -> Glyph:
    match state:
        case MergeState.CLEAN:
            return MERGE_CLEAN
        case MergeState.CONFLICT:
            return icon(PrState.CONFLICT)
        case MergeState.ERROR:
            return MERGE_ERROR
        case MergeState.UNKNOWN:
            return icon(PrState.UNKNOWN)
        case _ as unreachable:
            assert_never(unreachable)


def icon_for(member: Member | AgentView | None) -> Glyph:
    # Lazy on purpose: facts -> dispatch -> prs -> icons is a cycle, so this module cannot import facts at load time.
    from .facts import agent_icon_of

    if member is None:
        return icon(AgentIcon.OTHER)
    agent_type = (member.agent_type or "").lower()
    glyph, color = icon(agent_icon_of(member, agent_type))
    # A qa agent whose state is not known yet is drawn without a colour.
    if agent_type == "qa" and getattr(member, "qa_state", None) is None:
        return glyph, None
    return glyph, color
