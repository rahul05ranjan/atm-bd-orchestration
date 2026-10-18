from __future__ import annotations

from typing import assert_never

from .enums import AgentIcon, CiState, MergeState, PrState, SprintState
from .types import AgentView, Member

Icon = tuple[str, str | None]

AGENT_ICONS: dict[AgentIcon, Icon] = {
    AgentIcon.HAIKU: ("\U000f0156", "#FF6533"),
    AgentIcon.SONNET: ("\U000f0382", "#FF784D"),
    AgentIcon.OPUS: ("\U000f06c4", "#FF8C66"),
    AgentIcon.FABLE: ("\U000f0717", "#FF9F80"),
    AgentIcon.ASTRA: ("\U000f0674", "#FFE066"),
    AgentIcon.TERRA: ("\U000f01e7", "#2DE057"),
    AgentIcon.LUNA: ("\U000f0f65", "#FFE066"),
    AgentIcon.SOL: ("\U000f05a8", "#FFA60D"),
    AgentIcon.OTHER: ("·", None),
    AgentIcon.SEVERITY: ("\U000f01a6", "#E551BA"),
    AgentIcon.QA_RUNNING: ("\U000f029b", "#4A5AF8"),
    AgentIcon.QA_PASS: ("\U000f029b", "#2EA043"),
    AgentIcon.QA_FAIL: ("\U000f029b", "#F85149"),
}
SPRINT_ICONS: dict[SprintState, Icon] = {
    SprintState.OPEN: ("\U000f0766", "#2D81E0"),
    SprintState.BLOCKED: ("\U000f033e", "#FA8537"),
    SprintState.IN_PROGRESS: ("\U000f051f", "#0077FF"),
    SprintState.DEFERRED: ("\U000f03e5", "#FFB726"),
    SprintState.CLOSED: ("\U000f05e0", "#2DE057"),
    SprintState.PINNED: ("\U000f0403", "#42C3AB"),
    SprintState.HOOKED: ("\U000f06e2", "#2DE057"),
    SprintState.ACCEPTED: ("\U000f0513", "#E551BA"),
    SprintState.PLANNED: ("\U000f00ef", "#FFB726"),
    SprintState.PROPOSED: ("\U000f06e8", "#FFB726"),
    SprintState.READY: ("\U000f040d", "#0077FF"),
    SprintState.NOT_READY: ("\U000f137c", "#D97757"),
    SprintState.UNKNOWN: ("?", "#2D81E0"),
}
CI_ICONS: dict[CiState, Icon] = {
    CiState.PASS: ("\U000f062c", "#2EA043"),
    CiState.FAIL_MACOS: ("\U000f0035", "#F85149"),
    CiState.FAIL_WINDOWS: ("\U000f05b3", "#F85149"),
    CiState.FAIL_LINUX: ("\U000f033d", "#F85149"),
    CiState.FAIL_BUG: ("\U000f00e4", "#F85149"),
    CiState.RUNNING: ("\U000f0898", "#58A6FF"),
    CiState.NONE: ("\U000f01d8", "#2D81E0"),
    CiState.UNKNOWN: ("?", "#2D81E0"),
}
PR_ICONS: dict[PrState, Icon] = {
    PrState.OPEN: ("\U000f04c2", "#2D81E0"),
    PrState.DRAFT: ("\U000f0cb6", "#FFA60D"),
    PrState.MERGED: ("\U000f062d", "#8957E5"),
    PrState.CLOSED: ("\U000f073a", "#F85149"),
    PrState.CONFLICT: ("\U000f062c", "#F85149"),
    PrState.NONE: ("-", None),
    PrState.UNKNOWN: ("?", "#2D81E0"),
}
BEAD_STATUS_ICONS: dict[SprintState, Icon] = {key: SPRINT_ICONS[key] for key in (
    SprintState.OPEN, SprintState.BLOCKED, SprintState.IN_PROGRESS, SprintState.DEFERRED, SprintState.CLOSED,
    SprintState.PINNED, SprintState.HOOKED, SprintState.ACCEPTED, SprintState.PLANNED, SprintState.PROPOSED,
    SprintState.READY,
)}
PR_STATE_ICONS = {key: PR_ICONS[key] for key in (PrState.OPEN, PrState.DRAFT, PrState.MERGED, PrState.CLOSED)}
CI_STATE_ICONS = CI_ICONS
MERGE_CLEAN: Icon = ("\U000f062d", "#2EA043")
MERGE_ERROR: Icon = ("\U000f062d", "#F85149")
STATUS_OK = "✅"
STATUS_FAIL = "⛔"
MARKER_RED = "❗"
MARKER_YELLOW = "⚠️"
MARKER_HARD = "💎"
MARKER_FAST = "⚡"
MARKER_QA = "🔍"
MARKER_FIX_QA = "🛠️"


def icon(member: AgentIcon | SprintState | CiState | PrState) -> Icon:
    match member:
        case AgentIcon():
            return AGENT_ICONS[member]
        case SprintState():
            return SPRINT_ICONS[member]
        case CiState():
            return CI_ICONS[member]
        case PrState():
            return PR_ICONS[member]
        case _ as unreachable:
            assert_never(unreachable)


def merge_icon(state: MergeState) -> Icon:
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


def icon_for(member: Member | AgentView | None) -> Icon:
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
