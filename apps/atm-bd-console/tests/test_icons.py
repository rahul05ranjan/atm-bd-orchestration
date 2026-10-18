from __future__ import annotations

from datetime import UTC, datetime

import pytest
from rich.cells import cell_len

from atm_bd_console.ci import ci_failure_key
from atm_bd_console.enums import AgentIcon, CiState, MergeState, PrState, QaState, SprintState
from atm_bd_console.icons import (
    AGENT_ICONS,
    CI_ICONS,
    PR_ICONS,
    SPRINT_ICONS,
    icon,
    icon_for,
    merge_icon,
)
from atm_bd_console.types import AgentView, Member


def test_model_icons_are_case_insensitive_and_use_harness_fallbacks() -> None:
    cases = (
        (Member("a0", "active", None, "dev", "GPT-5.6-Sol", "codex-cli"), ("\U000f05a8", "#FFA60D")),
        (Member("a1", "active", None, "dev", "GPT-Luna", "codex-cli"), ("\U000f0f65", "#FFE066")),
        (Member("a", "active", None, "dev", "GPT-5.6-Terra", "codex-cli"), ("\U000f01e7", "#2DE057")),
        (Member("a2", "active", None, "dev", "Astra", "codex-cli"), ("\U000f0674", "#FFE066")),
        (Member("a3", "active", None, "dev", "GPT-5.6-Terra-Resolve", "codex-cli"),
         ("\U000f01e7", "#2DE057")),
        (Member("b0", "active", None, "dev", "Claude-Haiku", "claude-code"), ("\U000f0156", "#FF6533")),
        (Member("b1", "active", None, "dev", "Claude-Sonnet", "claude-code"), ("\U000f0382", "#FF784D")),
        (Member("b", "active", None, "dev", "Claude-Opus-5-5", "claude-code"), ("\U000f06c4", "#FF8C66")),
        (Member("b2", "active", None, "dev", "Claude-Fable", "claude-code"), ("\U000f0717", "#FF9F80")),
        (Member("c", "active", None, "dev", "unknown", "claude-code"), ("\U000f0382", "#FF784D")),
        (Member("d", "active", None, "dev", "unknown", "codex-cli"), ("\U000f01e7", "#2DE057")),
    )
    assert [icon_for(member) for member, _ in cases] == [expected for _, expected in cases]


def test_icon_for_looks_up_the_icon_facts_selects(monkeypatch) -> None:
    from atm_bd_console import facts

    qa = AgentView("qa", "idle", datetime(2026, 1, 1, tzinfo=UTC), 0, (), "qa", qa_state=QaState.PASS)
    monkeypatch.setattr(facts, "agent_icon_of", lambda member, step_kind: AgentIcon.OTHER)
    assert icon_for(qa) == icon(AgentIcon.OTHER)


def test_qa_icon_color_tracks_state_and_sanity_icon_is_fixed() -> None:
    qa = AgentView("qa", "idle", datetime(2026, 1, 1, tzinfo=UTC), 0, (), "qa")
    assert icon_for(qa) == ("\U000f029b", None)
    assert icon_for(AgentView("qa", "busy", qa.status_since, 0, (), "qa", qa_state=QaState.RUNNING)) == (
        "\U000f029b", "#4A5AF8",
    )
    assert icon_for(AgentView("qa", "idle", qa.status_since, 0, (), "qa", qa_state=QaState.PASS)) == (
        "\U000f029b", "#2EA043",
    )
    assert icon_for(AgentView("qa", "idle", qa.status_since, 0, (), "qa", qa_state=QaState.FAIL)) == (
        "\U000f029b", "#F85149",
    )
    assert icon_for(Member("sanity", "idle", None, "sanity")) == ("\U000f01a6", "#E551BA")


@pytest.mark.parametrize("model", ["claude", "codex", "unknown"])
@pytest.mark.parametrize(("harness", "expected"), [
    ("claude-code", ("\U000f0382", "#FF784D")),
    ("codex-cli", ("\U000f01e7", "#2DE057")),
])
def test_unmatched_models_use_harness_fallback(model: str, harness: str, expected: tuple[str, str]) -> None:
    assert icon_for(Member("agent", "idle", None, "dev", model, harness)) == expected


@pytest.mark.parametrize("harness", ["claude-code", "codex-cli"])
@pytest.mark.parametrize("model", ["haiku", "sonnet", "opus", "fable", "sol", "luna", "terra", "astra", "unknown"])
@pytest.mark.parametrize(("agent_type", "qa_state", "expected"), [
    ("qa", None, ("\U000f029b", None)),
    ("qa", QaState.RUNNING, ("\U000f029b", "#4A5AF8")),
    ("qa", QaState.PASS, ("\U000f029b", "#2EA043")),
    ("qa", QaState.FAIL, ("\U000f029b", "#F85149")),
    ("sanity", None, ("\U000f01a6", "#E551BA")),
])
def test_qa_and_sanity_icons_override_harness_and_model(
    harness: str, model: str, agent_type: str, qa_state: QaState | None, expected: tuple[str, str | None],
) -> None:
    agent = AgentView("agent", "idle", datetime(2026, 1, 1, tzinfo=UTC), 0, (), agent_type, model, harness, qa_state)
    assert icon_for(agent) == expected


def test_status_and_github_icon_tables_use_single_cell_glyphs() -> None:
    tables = (SPRINT_ICONS, PR_ICONS, CI_ICONS)
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
        for state in (None, QaState.RUNNING, QaState.PASS, QaState.FAIL)
    )
    glyphs.extend((
        icon_for(Member("sanity", "idle", None, "sanity"))[0],
        icon_for(None)[0],
    ))
    assert glyphs
    assert len({cell_len(glyph) for glyph in glyphs}) == 1
    assert {state: merge_icon(state) for state in MergeState} == {
        MergeState.CLEAN: ("\U000f062d", "#2EA043"),
        MergeState.CONFLICT: ("\U000f062c", "#F85149"),
        MergeState.ERROR: ("\U000f062d", "#F85149"),
        MergeState.UNKNOWN: ("?", "#2D81E0"),
    }


def test_enum_icon_tables_are_complete_and_exact():
    expected_agents = {
        AgentIcon.HAIKU: ("\U000f0156", "#FF6533"), AgentIcon.SONNET: ("\U000f0382", "#FF784D"),
        AgentIcon.OPUS: ("\U000f06c4", "#FF8C66"), AgentIcon.FABLE: ("\U000f0717", "#FF9F80"),
        AgentIcon.ASTRA: ("\U000f0674", "#FFE066"), AgentIcon.TERRA: ("\U000f01e7", "#2DE057"),
        AgentIcon.LUNA: ("\U000f0f65", "#FFE066"), AgentIcon.SOL: ("\U000f05a8", "#FFA60D"),
        AgentIcon.OTHER: ("·", None), AgentIcon.SEVERITY: ("\U000f01a6", "#E551BA"),
        AgentIcon.QA_RUNNING: ("\U000f029b", "#4A5AF8"), AgentIcon.QA_PASS: ("\U000f029b", "#2EA043"),
        AgentIcon.QA_FAIL: ("\U000f029b", "#F85149"),
    }
    expected_sprints = {
        SprintState.OPEN: ("\U000f0766", "#2D81E0"), SprintState.IN_PROGRESS: ("\U000f051f", "#0077FF"),
        SprintState.BLOCKED: ("\U000f033e", "#FA8537"), SprintState.DEFERRED: ("\U000f03e5", "#FFB726"),
        SprintState.CLOSED: ("\U000f05e0", "#2DE057"), SprintState.PINNED: ("\U000f0403", "#42C3AB"),
        SprintState.HOOKED: ("\U000f06e2", "#2DE057"), SprintState.ACCEPTED: ("\U000f0513", "#E551BA"),
        SprintState.PLANNED: ("\U000f00ef", "#FFB726"), SprintState.PROPOSED: ("\U000f06e8", "#FFB726"),
        SprintState.READY: ("\U000f040d", "#0077FF"), SprintState.NOT_READY: ("\U000f137c", "#D97757"),
        SprintState.UNKNOWN: ("?", "#2D81E0"),
    }
    expected_ci = {
        CiState.NONE: ("\U000f01d8", "#2D81E0"), CiState.RUNNING: ("\U000f0898", "#58A6FF"),
        CiState.PASS: ("\U000f062c", "#2EA043"), CiState.FAIL_MACOS: ("\U000f0035", "#F85149"),
        CiState.FAIL_WINDOWS: ("\U000f05b3", "#F85149"), CiState.FAIL_LINUX: ("\U000f033d", "#F85149"),
        CiState.FAIL_BUG: ("\U000f00e4", "#F85149"), CiState.UNKNOWN: ("?", "#2D81E0"),
    }
    expected_prs = {
        PrState.NONE: ("-", None), PrState.OPEN: ("\U000f04c2", "#2D81E0"),
        PrState.DRAFT: ("\U000f0cb6", "#FFA60D"), PrState.MERGED: ("\U000f062d", "#8957E5"),
        PrState.CLOSED: ("\U000f073a", "#F85149"), PrState.CONFLICT: ("\U000f062c", "#F85149"),
        PrState.UNKNOWN: ("?", "#2D81E0"),
    }
    for enum, table, expected in ((AgentIcon, AGENT_ICONS, expected_agents), (SprintState, SPRINT_ICONS, expected_sprints),
                                  (CiState, CI_ICONS, expected_ci), (PrState, PR_ICONS, expected_prs)):
        assert set(table) == set(enum)
        assert table == expected
        assert all(icon(member) == expected[member] for member in enum)


@pytest.mark.parametrize(
    ("failed_checks", "key"),
    [
        (("build (macos-14)",), "fail_macos"),
        (("test (windows-latest)",), "fail_windows"),
        (("win-x64 smoke",), "fail_windows"),
        (("ubuntu-22.04 pytest",), "fail_linux"),
        (("linux-arm64",), "fail_linux"),
        (("build (macos-14)", "test (ubuntu-22.04)"), "fail_bug"),
        (("build (macos-14)", "lint"), "fail_bug"),
        (("lint",), "fail_bug"),
        ((), "fail_bug"),
    ],
    ids=["macos", "windows", "win-prefix", "ubuntu", "linux", "two-platforms", "platform-plus-general", "general-only", "no-checks"],
)
def test_failed_ci_checks_classify_to_one_platform_or_the_bug_icon(failed_checks: tuple[str, ...], key: str) -> None:
    assert ci_failure_key(failed_checks) == key
