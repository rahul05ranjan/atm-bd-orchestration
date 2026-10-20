import pytest

from atm_bd_console.atm_source import sprint_state_of
from atm_bd_console.enums import FAILING_CI, AgentIcon, CiState, MergeState, PrState, SprintState
from atm_bd_console.facts import agent_icon_of
from atm_bd_console.github_source import ci_state_of, pr_state_of
from atm_bd_console.icons import AGENT_ICONS, CI_ICONS, PR_ICONS, SPRINT_ICONS, icon
from atm_bd_console.types import Member


@pytest.mark.parametrize("enum", (SprintState, CiState, PrState, MergeState))
def test_unknown_enum_value_is_rejected(enum):
    with pytest.raises(ValueError):
        enum("bogus")


def test_enum_members_cover_the_contract():
    assert {member.value for member in AgentIcon} == {
        "fable", "haiku", "sonnet", "opus", "astra", "sol", "terra", "luna", "other", "sanity",
        "sanity_pass", "sanity_fail", "qa_running", "qa_pass", "qa_fail",
    }
    assert {member.value for member in SprintState} == {
        "open", "in_progress", "blocked", "deferred", "closed", "pinned", "hooked", "accepted", "planned",
        "proposed", "ready", "not_ready", "unknown",
    }
    assert {member.value for member in CiState} == {
        "none", "running", "pass", "fail_macos", "fail_windows", "fail_linux", "fail_bug", "unknown",
    }
    assert {member.value for member in PrState} == {"none", "open", "draft", "merged", "closed", "conflict", "unknown"}
    assert {member.value for member in MergeState} == {"clean", "conflict", "head not pushed", "base deleted", "error", "unknown"}


def test_icon_tables_cover_each_enum_without_cross_type_collisions():
    assert set(AGENT_ICONS) == set(AgentIcon)
    assert set(SPRINT_ICONS) == set(SprintState)
    assert set(CI_ICONS) == set(CiState)
    assert set(PR_ICONS) == set(PrState)
    assert icon(SprintState.OPEN) != icon(PrState.OPEN)
    assert icon(CiState.NONE) != icon(PrState.NONE)


def test_source_adapters_handle_unknown_and_failure_precedence():
    assert sprint_state_of("blocked") is SprintState.BLOCKED
    assert sprint_state_of("unrecognized") is SprintState.UNKNOWN
    assert pr_state_of("OPEN", True) is PrState.DRAFT
    assert pr_state_of("unexpected", False) is PrState.UNKNOWN
    assert ci_state_of("PENDING", "IN_PROGRESS", ("test (macos-14)",)) is CiState.FAIL_MACOS
    assert ci_state_of("CANCELLED", None, ("lint",)) is CiState.FAIL_BUG
    assert ci_state_of("SUCCESS", "COMPLETED", ()) is CiState.PASS
    assert ci_state_of("unexpected", None, ()) is CiState.UNKNOWN


def test_failing_ci_is_defined_once_and_names_every_fail_state():
    assert FAILING_CI == {CiState.FAIL_MACOS, CiState.FAIL_WINDOWS, CiState.FAIL_LINUX, CiState.FAIL_BUG}
    assert {state for state in CiState if state.value.startswith("fail")} == FAILING_CI
    assert CiState.PASS not in FAILING_CI and CiState.UNKNOWN not in FAILING_CI


@pytest.mark.parametrize(
    ("model", "harness", "expected"),
    [
        ("GPT-5.6-Terra", "codex-cli", AgentIcon.TERRA),
        ("GPT-5.6-Terra", "claude-code", AgentIcon.TERRA),
        ("Claude-Opus-5-5", "claude-code", AgentIcon.OPUS),
        ("claude-opus-5-5", "codex-cli", AgentIcon.OPUS),
        ("claude-sonnet-opus", "claude-code", AgentIcon.SONNET),
        ("claude", "claude-code", AgentIcon.SONNET),
        ("codex", "codex-cli", AgentIcon.TERRA),
        ("unknown", "claude-code", AgentIcon.SONNET),
        ("unknown", "codex-cli", AgentIcon.TERRA),
        ("unknown", "foo", AgentIcon.OTHER),
        (None, None, AgentIcon.OTHER),
    ],
)
def test_agent_icon_matches_model_names_by_contains_then_falls_back_by_harness(
    model: str | None, harness: str | None, expected: AgentIcon,
) -> None:
    member = Member("name", "active", None, "dev", model, harness)
    assert agent_icon_of(member, "dev") is expected


def test_agent_icon_uses_step_role_and_harness_fallback():
    member = Member("name", "active", None, "dev", "unknown", "codex-cli")
    assert agent_icon_of(member, "dev") is AgentIcon.TERRA
    assert agent_icon_of(member, "sanity") is AgentIcon.SANITY
    assert agent_icon_of(member, "qa") is AgentIcon.QA_RUNNING
