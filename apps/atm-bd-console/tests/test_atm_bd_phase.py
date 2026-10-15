"""D3: phase() over recorded `bd --json` output of real phase-bh beads (atm-core, 2026-10-14).

Fixtures in tests/fixtures/beads/, captured read-only from atm-core:
- list-open.json: `bd list -l phase-bh --json`, kept to the root, the plan's open sprint containers and
  group beads, the findings atm-bh-4.group-dev.3/.4 and atm-bh-6.group-qa-f1, and the QA bead atm-bh-6.group-qa-f1-qa.
- ready.json: `bd ready -l phase-bh --json`, kept to the same ids.
- show-closed.json: `bd show <the 12 plan-derived ids absent from the open list> --json`, verbatim.
The fake `bd` below only replays those files; beads_source and atm_bd run unchanged.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from atm_bd_console import beads_source
from atm_bd_console.atm_bd import closed_show_ids, group_ids, phase, phase_file
from atm_bd_console.result import ConfigInvalid, Err, NotFound, Ok
from atm_bd_console.types import Bead, Phase

FIXTURES = Path(__file__).parent / "fixtures" / "beads"
PLAN_FILE = phase_file(str(FIXTURES / "repo"), "bh")
# Hand-derived from docs/plans/phase-bh.jsonl: each container and its group-{dev,sanity,qa}, minus the open ones.
CLOSED_IDS = (
    "atm-bh-1.group-dev", "atm-bh-1.group-sanity", "atm-bh-1.group-qa", "atm-bh-2.group-dev",
    "atm-bh-3.group-dev", "atm-bh-3.group-sanity", "atm-bh-3.group-qa", "atm-bh-5.group-dev",
    "atm-bh-6.group-dev", "atm-bh-6.group-sanity", "atm-bh-6.group-qa", "atm-bh-7.group-dev",
)
PLAN_DEPENDS_ON = {
    "atm-bh-1": (), "atm-bh-2": (), **{f"atm-bh-{n}": ("atm-bh-1",) for n in range(3, 9)},
    "atm-bh-9": ("atm-bh-3", "atm-bh-4", "atm-bh-5", "atm-bh-6", "atm-bh-7", "atm-bh-8"), "atm-bh-10": ("atm-bh-9",),
}


def _replay_bd(fake_bin) -> None:
    fake_bin.make("bd", (
        'case "$1" in\n'
        f'  list) cat "{FIXTURES / "list-open.json"}" ;;\n'
        f'  ready) cat "{FIXTURES / "ready.json"}" ;;\n'
        f'  show) cat "{FIXTURES / "show-closed.json"}" ;;\n'
        "  *) exit 9 ;;\n"
        "esac\n"
    ))


def _ok(result):
    assert isinstance(result, Ok), result
    return result.value


def _read(fake_bin) -> tuple[tuple[Bead, ...], tuple[Bead, ...], tuple[Bead, ...]]:
    """One tick's bead reads: open and ready by label, then one batched show of the plan-derived closed ids."""
    _replay_bd(fake_bin)
    file = _ok(PLAN_FILE)
    open_beads = _ok(beads_source.open_beads(file.label))
    ready = _ok(beads_source.ready(file.label))
    shown = _ok(beads_source.show(closed_show_ids(file, open_beads + ready)))
    return open_beads, ready, shown


def _root(beads: tuple[Bead, ...]) -> Bead:
    return next(bead for bead in beads if bead.id == "atm-phase-bh")


def _phase(open_beads, ready, shown) -> Phase:
    return _ok(phase(_ok(PLAN_FILE), _root(open_beads), open_beads, ready, shown))


def test_closed_ids_are_derived_from_the_plan_and_shown_in_one_batched_call(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    assert closed_show_ids(_ok(PLAN_FILE), open_beads + ready) == CLOSED_IDS
    assert fake_bin.argv() == [
        ("bd", "list", "-l", "phase-bh", "--json"),
        ("bd", "ready", "-l", "phase-bh", "--json"),
        ("bd", "show", *CLOSED_IDS, "--json"),
    ]
    # The recording was made with the ids in sorted order; bd returns them in request order.
    assert sorted(bead.id for bead in shown) == sorted(CLOSED_IDS)
    assert {bead.status for bead in shown} == {"closed"}


def test_closed_ids_cover_every_container_and_group_when_nothing_is_open() -> None:
    file = _ok(PLAN_FILE)
    ids = closed_show_ids(file, ())
    assert len(ids) == 40
    assert ids[:4] == ("atm-bh-1", "atm-bh-1.group-dev", "atm-bh-1.group-sanity", "atm-bh-1.group-qa")
    assert group_ids("atm-bh-10") == ("atm-bh-10.group-dev", "atm-bh-10.group-sanity", "atm-bh-10.group-qa")


def test_phase_bh_yields_ten_sprints_from_their_bead_metadata_and_the_plan(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    result = _phase(open_beads, ready, shown)
    raw = {item["id"]: item for item in json.loads((FIXTURES / "list-open.json").read_text())}
    assert [sprint.id for sprint in result.sprints] == [f"atm-bh-{n}" for n in range(1, 11)]
    for sprint in result.sprints:
        meta = raw[sprint.id]["metadata"]
        assert (sprint.layer, sprint.branch, sprint.target, sprint.stack) == (
            meta["layer"], meta["branch"], meta["pr_target"], meta["stack"])
        assert sprint.depends_on == PLAN_DEPENDS_ON[sprint.id]
        assert sprint.title == raw[sprint.id]["title"]
        assert sprint.closed is False
    bh4 = next(sprint for sprint in result.sprints if sprint.id == "atm-bh-4")
    assert (bh4.layer, bh4.branch, bh4.target) == (4, "sprint/bh-4-beads-source", "sprint/bh-1-contract")
    assert result.step_errors == ()
    assert (result.root_id, result.label, result.closed) == ("atm-phase-bh", "phase-bh", False)


def test_findings_count_the_sprints_finding_beads(fake_bin) -> None:
    result = _phase(*_read(fake_bin))
    findings = {sprint.id: sprint.findings for sprint in result.sprints}
    # atm-bh-4.group-dev.3/.4 hang under atm-bh-4.group-dev; atm-bh-6.group-qa-f1 is under the root with sprint_bead.
    assert findings == {**{f"atm-bh-{n}": 0 for n in range(1, 11)}, "atm-bh-4": 2, "atm-bh-6": 1}


def test_shown_closed_groups_are_steps_and_ready_marks_steps(fake_bin) -> None:
    result = _phase(*_read(fake_bin))
    steps = {step.bead_id: step for step in result.steps}
    for bead_id in CLOSED_IDS:
        assert steps[bead_id].status == "closed"
    assert steps["atm-bh-1.group-qa"].kind == "qa"
    assert steps["atm-bh-8.group-dev"].ready is True
    assert steps["atm-bh-4.group-qa"].ready is False
    assert not {"atm-phase-bh", *(f"atm-bh-{n}" for n in range(1, 11))} & set(steps)


def test_without_the_batched_show_closed_groups_are_absent(fake_bin) -> None:
    open_beads, ready, _ = _read(fake_bin)
    steps = {step.bead_id for step in _phase(open_beads, ready, ()).steps}
    assert not set(CLOSED_IDS) & steps


def test_planted_missing_sprint_is_reported_not_dropped(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    gone = "atm-bh-5"
    result = _phase(tuple(b for b in open_beads if b.id != gone), tuple(b for b in ready if b.id != gone), shown)
    assert [sprint.id for sprint in result.sprints] == [f"atm-bh-{n}" for n in range(1, 11) if n != 5]
    assert [bead_id for bead_id, _ in result.step_errors] == [gone]
    error = result.step_errors[0][1]
    assert isinstance(error, NotFound)
    assert error.what == f"sprint bead {gone} listed in the plan"
    assert "closed_show_ids" in error.fix


def test_planted_closed_sprint_comes_from_the_batched_show(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    bh2 = next(bead for bead in open_beads if bead.id == "atm-bh-2")
    open_beads = tuple(bead for bead in open_beads if bead.id != "atm-bh-2")
    ready = tuple(bead for bead in ready if bead.id != "atm-bh-2")
    assert "atm-bh-2" in closed_show_ids(_ok(PLAN_FILE), open_beads + ready)
    result = _phase(open_beads, ready, (*shown, replace(bh2, status="closed")))
    bh2_sprint = next(sprint for sprint in result.sprints if sprint.id == "atm-bh-2")
    assert bh2_sprint.closed is True
    assert bh2_sprint.layer == 2
    assert result.step_errors == ()


def test_planted_sprint_without_layer_metadata_is_a_step_error(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)

    def strip(bead: Bead) -> Bead:
        return replace(bead, metadata={k: v for k, v in bead.metadata.items() if k != "layer"}) \
            if bead.id == "atm-bh-3" else bead
    result = _phase(tuple(map(strip, open_beads)), tuple(map(strip, ready)), shown)
    assert "atm-bh-3" not in {sprint.id for sprint in result.sprints}
    assert len(result.sprints) == 9
    assert [(bead_id, error.what) for bead_id, error in result.step_errors] == [
        ("atm-bh-3", "atm-bh-3.metadata.layer")]


def test_unusable_root_is_err(fake_bin) -> None:
    open_beads, ready, shown = _read(fake_bin)
    file = _ok(PLAN_FILE)
    missing = phase(file, None, open_beads, ready, shown)
    assert isinstance(missing, Err) and isinstance(missing.error, NotFound)
    assert missing.error.what == "atm-phase-bh (no root bead)"
    wrong = phase(file, next(b for b in open_beads if b.id == "atm-bh-4"), open_beads, ready, shown)
    assert isinstance(wrong, Err) and isinstance(wrong.error, NotFound)
    assert wrong.error.what == "atm-phase-bh (root bead atm-bh-4)"
    unlabelled = replace(_root(open_beads), labels=("scope:epic",))
    mislabelled = phase(file, unlabelled, open_beads, ready, shown)
    assert isinstance(mislabelled, Err) and isinstance(mislabelled.error, ConfigInvalid)
    assert mislabelled.error.key == "atm-phase-bh.labels"
    for result in (missing, wrong, mislabelled):
        assert result.error.fix
