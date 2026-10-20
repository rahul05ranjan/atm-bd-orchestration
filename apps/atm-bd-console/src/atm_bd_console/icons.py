from __future__ import annotations

from typing import assert_never

from .agent_roles import agent_group
from .enums import AgentIcon, CiState, MergeState, PrState, QaState, SprintState
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
    AgentIcon.SANITY: ("\U000f01a6", "#E551BA"),
    AgentIcon.SANITY_PASS: ("\U000f01a6", "#2EA043"),
    AgentIcon.SANITY_FAIL: ("\U000f01a6", "#F85149"),
    AgentIcon.QA_RUNNING: ("\U000f029b", "#4A5AF8"),
    AgentIcon.QA_PASS: ("\U000f029b", "#2EA043"),
    AgentIcon.QA_FAIL: ("\U000f029b", "#F85149"),
}
QA_FAIL_NON_BLOCKING: Icon = (
    AGENT_ICONS[AgentIcon.QA_FAIL][0],
    AGENT_ICONS[AgentIcon.FABLE][1],
)
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
# Layout C individual/index.md: QA pass/fail use U+F029B (#2EA043/#F85149).
# Lead ruling 2026-10-19 applies those entries to sanity verdicts as well.
# Layout C has no API_FAILURE entry; the lead ruled to retain U+F0026 #D29922.
LLM_VERDICT_ICONS: dict[str, Icon] = {
    "PASS": AGENT_ICONS[AgentIcon.QA_PASS], "FAIL": AGENT_ICONS[AgentIcon.QA_FAIL],
    "API_FAILURE": ("\U000f0026", "#D29922"),
}
JEV_VERDICT_ICONS: dict[str, Icon] = {
    "PASS": AGENT_ICONS[AgentIcon.SANITY_PASS], "FAIL": AGENT_ICONS[AgentIcon.SANITY_FAIL],
    "API_FAILURE": ("\U000f0026", "#D29922"),
}


def qa_run_icon(state: QaState | None, blocking: int) -> Icon | None:
    """Return the one QA-run verdict icon, including the non-blocking FAIL colour."""
    if state == QaState.PASS:
        return AGENT_ICONS[AgentIcon.QA_PASS]
    if state == QaState.FAIL:
        return AGENT_ICONS[AgentIcon.QA_FAIL] if blocking else QA_FAIL_NON_BLOCKING
    return None


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
    agent_type = agent_group(member.agent_type)
    glyph, color = icon(agent_icon_of(member, agent_type))
    # A qa agent whose state is not known yet is drawn without a colour.
    if agent_type == "qa" and getattr(member, "qa_state", None) is None:
        return glyph, None
    return glyph, color
