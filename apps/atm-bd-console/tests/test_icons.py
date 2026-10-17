from __future__ import annotations

from datetime import UTC, datetime

from rich.cells import cell_len

from atm_bd_console.icons import (
    BEAD_STATUS_ICONS,
    CI_STATE_ICONS,
    MERGE_STATE_ICONS,
    PR_STATE_ICONS,
    SPRINT_STATE_ICONS,
    icon_for,
)
from atm_bd_console.types import AgentView, Member


def test_model_icons_are_case_insensitive_and_use_harness_fallbacks() -> None:
    cases = (
        (Member("a0", "active", None, "dev", "GPT-5.6-Sol", "codex-cli"), ("\U000f05a8", "#FFA60D")),
        (Member("a1", "active", None, "dev", "GPT-Luna", "codex-cli"), ("\U000f0f65", "#FFE066")),
        (Member("a", "active", None, "dev", "GPT-5.6-Terra", "codex-cli"), ("\U000f01e7", "#2DE057")),
        (Member("a2", "active", None, "dev", "Astra", "codex-cli"), ("\U000f0674", "#FFE066")),
        (Member("b0", "active", None, "dev", "Claude-Haiku", "claude-code"), ("\U000f0156", "#FF6533")),
        (Member("b1", "active", None, "dev", "Claude-Sonnet", "claude-code"), ("\U000f0382", "#FF784D")),
        (Member("b", "active", None, "dev", "Claude-Opus-5-5", "claude-code"), ("\U000f06c4", "#FF8C66")),
        (Member("b2", "active", None, "dev", "Claude-Fable", "claude-code"), ("\U000f0717", "#FF9F80")),
        (Member("c", "active", None, "dev", "unknown", "claude-code"), ("\U000f0382", "#FF784D")),
        (Member("d", "active", None, "dev", "unknown", "codex-cli"), ("\U000f01e7", "#2DE057")),
    )
    assert [icon_for(member) for member, _ in cases] == [expected for _, expected in cases]


def test_qa_icon_color_tracks_state_and_sanity_icon_is_fixed() -> None:
    qa = AgentView("qa", "idle", datetime(2026, 1, 1, tzinfo=UTC), 0, (), "qa")
    assert icon_for(qa) == ("\U000f029b", None)
    assert icon_for(AgentView("qa", "busy", qa.status_since, 0, (), "qa", qa_state="running")) == (
        "\U000f029b", "#4A5AF8",
    )
    assert icon_for(AgentView("qa", "idle", qa.status_since, 0, (), "qa", qa_state="pass")) == (
        "\U000f029b", "#2EA043",
    )
    assert icon_for(AgentView("qa", "idle", qa.status_since, 0, (), "qa", qa_state="fail")) == (
        "\U000f029b", "#F85149",
    )
    assert icon_for(Member("sanity", "idle", None, "sanity")) == ("\U000f01a6", "#E551BA")


def test_status_and_github_icon_tables_use_single_cell_glyphs() -> None:
    tables = (BEAD_STATUS_ICONS, SPRINT_STATE_ICONS, PR_STATE_ICONS, CI_STATE_ICONS, MERGE_STATE_ICONS)
    glyphs = [glyph for table in tables for glyph, _ in table.values()]
    model_members = tuple(
        Member(f"claude-{model}", "active", None, "dev", model, "claude-code")
        for model in ("haiku", "sonnet", "opus", "fable", "unknown")
    ) + tuple(
        Member(f"codex-{model}", "active", None, "dev", model, "codex-cli")
        for model in ("sol", "luna", "terra", "astra", "unknown")
    )
    glyphs.extend(icon_for(member)[0] for member in model_members)
    glyphs.extend(
        icon_for(AgentView("qa", "idle", datetime(2026, 1, 1, tzinfo=UTC), 0, (), "qa", qa_state=state))[0]
        for state in (None, "running", "pass", "fail")
    )
    glyphs.extend((
        icon_for(Member("sanity", "idle", None, "sanity"))[0],
        icon_for(None)[0],
    ))
    assert glyphs
    assert len({cell_len(glyph) for glyph in glyphs}) == 1
    assert BEAD_STATUS_ICONS == {
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
    assert SPRINT_STATE_ICONS["not_ready"] == ("\U000f137c", "#D97757")
    assert PR_STATE_ICONS["draft"] == ("\U000f0cb6", "#FFA60D")
    assert CI_STATE_ICONS["none"] == ("\U000f01d8", "#2D81E0")
    assert MERGE_STATE_ICONS["conflict"] == ("\U000f062d", "#F85149")
