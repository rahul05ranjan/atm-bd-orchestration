from __future__ import annotations

from dataclasses import is_dataclass

from atm_bd_console import result, types


def test_contract_exports_are_frozen_dataclasses() -> None:
    names = (
        ["Ok", "Err", "CommandFailed", "Timeout", "Rejected", "Unreachable", "ParseFailed", "NotFound", "ConfigInvalid", "LockHeld", "IoFailed"]
        + ["Member", "Task", "Bead", "Step", "Sprint", "Phase", "DispatchSpec", "RemoteRef", "Merge", "Worktree", "PullRequest", "GhStatus", "RepoConfig", "Config", "DispatchContext", "Inputs", "Row", "AgentView", "SectionRow", "Section", "StepView", "SprintView", "Problem", "Action", "PrRow", "Metrics", "Due", "Snapshot", "State", "PhaseFile", "MergeFile", "PrsFile", "ActionLine"]
    )
    for name in names:
        value = getattr(result, name, None) or getattr(types, name)
        assert is_dataclass(value)
        assert value.__dataclass_params__.frozen
