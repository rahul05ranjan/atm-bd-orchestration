from __future__ import annotations

AGENT_GROUPS = ("lead", "dev", "sanity", "qa", "other")


def agent_group(agent_type: str | None) -> str:
    role = (agent_type or "").strip().lower()
    if role == "sprint_qa":
        return "qa"
    return role if role in AGENT_GROUPS else "other"
