import pytest

from atm_bd_console.enums import AgentIcon, CiState, MergeState, PrState, SprintState
from atm_bd_console.icons import AGENT_ICONS, CI_ICONS, PR_ICONS, SPRINT_ICONS, icon


@pytest.mark.parametrize("enum", (SprintState, CiState, PrState, MergeState))
def test_unknown_enum_value_is_rejected(enum):
    with pytest.raises(ValueError):
        enum("bogus")


def test_enum_members_cover_the_contract():
    assert {member.value for member in AgentIcon} == {
        "fable", "haiku", "sonnet", "opus", "astra", "sol", "terra", "luna", "other", "severity",
        "qa_running", "qa_pass", "qa_fail",
    }
    assert {member.value for member in SprintState} == {
        "open", "in_progress", "blocked", "deferred", "closed", "pinned", "hooked", "accepted", "planned",
        "proposed", "ready", "not_ready", "unknown",
    }
    assert {member.value for member in CiState} == {
        "none", "running", "pass", "fail_macos", "fail_windows", "fail_linux", "fail_bug", "unknown",
    }
    assert {member.value for member in PrState} == {"none", "open", "draft", "merged", "closed", "conflict", "unknown"}
    assert {member.value for member in MergeState} == {"clean", "conflict", "error", "unknown"}


def test_icon_tables_cover_each_enum_without_cross_type_collisions():
    assert set(AGENT_ICONS) == set(AgentIcon)
    assert set(SPRINT_ICONS) == set(SprintState)
    assert set(CI_ICONS) == set(CiState)
    assert set(PR_ICONS) == set(PrState)
    assert icon(SprintState.OPEN) != icon(PrState.OPEN)
    assert icon(CiState.NONE) != icon(PrState.NONE)
