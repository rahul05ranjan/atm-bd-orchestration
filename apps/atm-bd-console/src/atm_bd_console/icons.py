from __future__ import annotations

from .enums import AgentIcon, CiState, PrState, SprintState
from .types import AgentView, Member

Icon = tuple[str, str | None]

_CLAUDE_MODELS: dict[str, Icon] = {
    "haiku": ("\U000f0156", "#FF6533"),
    "sonnet": ("\U000f0382", "#FF784D"),
    "opus": ("\U000f06c4", "#FF8C66"),
    "fable": ("\U000f0717", "#FF9F80"),
}
_CODEX_MODELS: dict[str, Icon] = {
    "astra": ("\U000f0674", "#FFE066"),
    "terra": ("\U000f01e7", "#2DE057"),
    "luna": ("\U000f0f65", "#FFE066"),
    "sol": ("\U000f05a8", "#FFA60D"),
}
_QA_STYLES = {"running": "#4A5AF8", "pass": "#2EA043", "fail": "#F85149"}


def icon_for(member: Member | AgentView | None) -> Icon:
    """Choose the roster icon and color for one agent or an unknown roster entry."""
    if member is None:
        return "·", None
    agent_type = (member.agent_type or "").lower()
    if agent_type == "qa":
        return "\U000f029b", _QA_STYLES.get(getattr(member, "qa_state", None))
    if agent_type == "sanity":
        return "\U000f01a6", "#E551BA"

    harness = (member.harness or "").lower()
    model = (member.model or "").lower()
    if harness == "claude-code":
        icons = _CLAUDE_MODELS
        fallback = icons["sonnet"]
    elif harness == "codex-cli":
        icons = _CODEX_MODELS
        fallback = icons["terra"]
    else:
        return "·", None
    return next((icon for name, icon in icons.items() if name in model), fallback)


BEAD_STATUS_ICONS: dict[str, Icon] = {
    "open": ("\U000f0766", "#2D81E0"),
    "blocked": ("\U000f033e", "#FA8537"),
    "in_progress": ("\U000f051f", "#0077FF"),
    "deferred": ("\U000f03e5", "#FFB726"),
    "closed": ("\U000f05e0", "#2DE057"),
    "pinned": ("\U000f0403", "#42C3AB"),
    "hooked": ("\U000f06e2", "#2DE057"),
    "accepted": ("\U000f0513", "#E551BA"),
    "planned": ("\U000f00ef", "#FFB726"),
    "proposed": ("\U000f06e8", "#FFB726"),
    "ready": ("\U000f040d", "#0077FF"),
}
SPRINT_STATE_ICONS: dict[str, Icon] = {
    "closed": BEAD_STATUS_ICONS["closed"],
    "in_progress": BEAD_STATUS_ICONS["in_progress"],
    "ready": BEAD_STATUS_ICONS["ready"],
    "not_ready": ("\U000f137c", "#D97757"),
}
PR_STATE_ICONS: dict[str, Icon] = {
    "open": ("\U000f04c2", "#2D81E0"),
    "draft": ("\U000f0cb6", "#FFA60D"),
    "merged": ("\U000f062d", "#8957E5"),
    "closed": ("\U000f073a", "#F85149"),
}
CI_STATE_ICONS: dict[str, Icon] = {
    "success": ("\U000f062c", "#2EA043"),
    "fail_macos": ("\U000f0035", "#F85149"),
    "fail_windows": ("\U000f05b3", "#F85149"),
    "fail_linux": ("\U000f033d", "#F85149"),
    "fail_bug": ("\U000f00e4", "#F85149"),
    "pending": ("\U000f0898", "#58A6FF"),
    "none": ("\U000f01d8", "#2D81E0"),
}
MERGE_STATE_ICONS: dict[str, Icon] = {
    "clean": ("\U000f062d", "#2EA043"),
    "conflict": ("\U000f062c", "#F85149"),
    "error": ("\U000f062d", "#F85149"),
}

AGENT_ICONS: dict[AgentIcon, Icon] = {
    **{AgentIcon(key): value for key, value in _CLAUDE_MODELS.items()},
    **{AgentIcon(key): value for key, value in _CODEX_MODELS.items()},
    AgentIcon.OTHER: ("·", None),
    AgentIcon.SEVERITY: ("\U000f01a6", "#E551BA"),
    AgentIcon.QA_RUNNING: ("\U000f029b", "#4A5AF8"),
    AgentIcon.QA_PASS: ("\U000f029b", "#2EA043"),
    AgentIcon.QA_FAIL: ("\U000f029b", "#F85149"),
}
SPRINT_ICONS: dict[SprintState, Icon] = {
    **{SprintState(key): value for key, value in BEAD_STATUS_ICONS.items()},
    SprintState.NOT_READY: ("\U000f137c", "#D97757"),
    SprintState.UNKNOWN: ("?", "#F85149"),
}
CI_ICONS: dict[CiState, Icon] = {
    CiState.NONE: CI_STATE_ICONS["none"],
    CiState.RUNNING: CI_STATE_ICONS["pending"],
    CiState.PASS: CI_STATE_ICONS["success"],
    CiState.FAIL_MACOS: CI_STATE_ICONS["fail_macos"],
    CiState.FAIL_WINDOWS: CI_STATE_ICONS["fail_windows"],
    CiState.FAIL_LINUX: CI_STATE_ICONS["fail_linux"],
    CiState.FAIL_BUG: CI_STATE_ICONS["fail_bug"],
    CiState.UNKNOWN: ("?", "#F85149"),
}
PR_ICONS: dict[PrState, Icon] = {
    **{PrState(key): value for key, value in PR_STATE_ICONS.items()},
    PrState.NONE: ("-", None),
    PrState.CONFLICT: MERGE_STATE_ICONS["conflict"],
    PrState.UNKNOWN: ("?", "#F85149"),
}


def icon(member: AgentIcon | SprintState | CiState | PrState) -> Icon:
    if type(member) is AgentIcon:
        return AGENT_ICONS[member]
    if type(member) is SprintState:
        return SPRINT_ICONS[member]
    if type(member) is CiState:
        return CI_ICONS[member]
    if type(member) is PrState:
        return PR_ICONS[member]
    raise TypeError(f"unsupported icon: {member!r}")
_FAILED_PLATFORMS = (
    ("fail_macos", ("macos", "darwin")),
    ("fail_windows", ("windows", "win-", "win_")),
    ("fail_linux", ("ubuntu", "linux")),
)


def ci_failure_key(failed_checks: tuple[str, ...]) -> str:
    """Return the CI_STATE_ICONS key for a red PR: one failing platform, else the general bug icon."""
    platforms: set[str] = set()
    for name in failed_checks:
        lowered = name.lower()
        platform = next((key for key, words in _FAILED_PLATFORMS if any(word in lowered for word in words)), None)
        if platform is None:
            return "fail_bug"
        platforms.add(platform)
    return platforms.pop() if len(platforms) == 1 else "fail_bug"
