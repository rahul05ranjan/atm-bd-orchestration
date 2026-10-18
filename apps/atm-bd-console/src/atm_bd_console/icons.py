from __future__ import annotations

from .types import AgentView, Member

Icon = tuple[str, str | None]

_CLAUDE_MODELS: dict[str, Icon] = {
    "haiku": ("\U000f0156", "#FF6533"),
    "sonnet": ("\U000f0382", "#FF784D"),
    "opus": ("\U000f06c4", "#FF8C66"),
    "fable": ("\U000f0717", "#FF9F80"),
}
_CODEX_MODELS: dict[str, Icon] = {
    "sol": ("\U000f05a8", "#FFA60D"),
    "luna": ("\U000f0f65", "#FFE066"),
    "terra": ("\U000f01e7", "#2DE057"),
    "astra": ("\U000f0674", "#FFE066"),
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
    "failure": ("\U000f062c", "#F85149"),
    "pending": ("\U000f0898", "#58A6FF"),
    "none": ("\U000f01d8", "#2D81E0"),
}
MERGE_STATE_ICONS: dict[str, Icon] = {
    "clean": ("\U000f062d", "#2EA043"),
    "conflict": ("\U000f062d", "#F85149"),
    "error": ("\U000f062d", "#F85149"),
}
