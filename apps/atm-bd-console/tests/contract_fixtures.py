"""Builds tests/fixtures/contract/* with to_json from populated contract instances.

Run `uv run python tests/contract_fixtures.py` from apps/atm-bd-console to rewrite the files after a contract layer
changes; test_fixtures asserts the committed files equal this output.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from contract_samples import (
    INPUTS,
    MERGE,
    METRICS,
    NOW,
    PRS_FILE,
    PULL_REQUEST,
    REJECTED,
    SNAPSHOT,
    TIMEOUT,
)

from atm_bd_console.codec import to_json
from atm_bd_console.enums import CiState, MergeState
from atm_bd_console.types import ActionLine, GhStatus, MergeFile

FIXTURES = Path(__file__).parent / "fixtures" / "contract"

GH_RUNNING = GhStatus("graphql", True, None, NOW, False)
GH_OFF = GhStatus("off", None, None, None, False)
GH_ERR = GhStatus("graphql", False, REJECTED, NOW, False)

RUNNING = replace(SNAPSHOT, root_closed=False, stopped=None, gh=GH_RUNNING, top_ci=CiState.RUNNING)
SNAPSHOTS = {
    "snapshot-running.json": RUNNING,
    "snapshot-stopped-closed.json": replace(
        RUNNING, root_closed=True, stopped="closed", metrics=replace(METRICS, done=11, pct=100), top_ci=CiState.PASS,
    ),
    "snapshot-stopped-idle.json": replace(RUNNING, stopped="idle"),
    "snapshot-gh-off.json": replace(RUNNING, gh=GH_OFF, top_ci=None, errors=(TIMEOUT,)),
    "snapshot-gh-err.json": replace(RUNNING, gh=GH_ERR, top_ci=None, errors=(REJECTED,)),
}
ACTION_LINES = (
    ActionLine(NOW, "assign", "quality-mgr", "atm-bh-6.qa", "QA bh-6 round 2"),
    ActionLine(NOW, "send", "arch-ctm", "atm-bh-6", "atm-bh-6 has been assigned for 30m without a start"),
    ActionLine(NOW, "batch", "fenix", None, "2 problems: CI red on #1850; merge conflict on sprint/bh-6-tick-rules"),
    ActionLine(NOW, "hold", "arch-ctm", "atm-bh-7", "holding: CI red on the layer below"),
    ActionLine(NOW, "dry_run", None, None, "would assign atm-bh-6.qa to quality-mgr"),
)


def fixtures() -> dict[str, str]:
    files = {name: to_json(snapshot) + "\n" for name, snapshot in SNAPSHOTS.items()}
    files["inputs.json"] = to_json(INPUTS) + "\n"
    files["merge.json"] = to_json(MergeFile(NOW, {MERGE.branch: MERGE, "sprint/bh-4-dispatch": replace(
        MERGE, branch="sprint/bh-4-dispatch", state=MergeState.CLEAN, files=(), messages=())})) + "\n"
    files["prs.json"] = to_json(replace(PRS_FILE, gh=GH_RUNNING, prs=(PULL_REQUEST, replace(
        PULL_REQUEST, number=1851, head="sprint/bh-7-runner", head_sha="fedcba9876543210", ci=CiState.PASS,
        failed_checks=())))) + "\n"
    files["actions.jsonl"] = "".join(to_json(line) + "\n" for line in ACTION_LINES)
    return files


if __name__ == "__main__":
    for name, text in fixtures().items():
        (FIXTURES / name).write_text(text)
