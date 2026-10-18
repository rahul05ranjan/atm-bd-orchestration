"""beads_source against a fake bd that answers `bd show <ids> --json` from one JSON file per bead."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atm_bd_console import beads_source
from atm_bd_console.enums import SprintState
from atm_bd_console.result import CommandFailed, Err, Ok, ParseFailed
from atm_bd_console.types import Bead

NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize(("raw", "expected"), [("open", SprintState.OPEN), ("closed", SprintState.CLOSED),
                                                ("unexpected", SprintState.UNKNOWN)])
def test_bd_status_is_typed_and_preserves_raw_value(raw: str, expected: SprintState) -> None:
    bead = beads_source._bead({"id": "x", "title": "x", "status": raw, "created_at": "2026-10-15T04:00:00Z"})
    assert bead.status is expected
    assert bead.raw_status == raw


# Each `bd show` argument names a file under $FAKE_BEADS; the reply is their JSON array, as bd prints it.
SHOW = """
[ "$1" = show ] || { echo "unexpected: $*" >&2; exit 2; }
shift
printf '['
sep=''
for id in "$@"; do
  [ "$id" = --json ] && continue
  [ -f "$FAKE_BEADS/$id.json" ] || { echo "no issue found: $id" >&2; exit 1; }
  printf '%s' "$sep"; cat "$FAKE_BEADS/$id.json"; sep=','
done
printf ']'
"""


def leaf(bead_id: str, parent: str | None) -> Bead:
    return Bead(bead_id, bead_id, SprintState.OPEN, ("phase-bh",), parent, (), None, {}, NOW, None, None)


@pytest.fixture
def beads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The chain atm-phase-bh <- atm-bh-4 <- atm-bh-4.group <- the leaf steps, as closed beads bd show returns."""
    store = tmp_path / "beads"
    store.mkdir()
    for bead_id, parent in (("atm-phase-bh", None), ("atm-bh-4", "atm-phase-bh"), ("atm-bh-4.group", "atm-bh-4"),
                            ("atm-bh-6", "atm-phase-bh")):
        raw = {"id": bead_id, "title": bead_id, "status": "closed", "labels": ["phase-bh"],
               "created_at": "2026-10-14T20:00:00-07:00", "closed_at": "2026-10-15T01:00:00Z"}
        if bead_id == "atm-bh-4":
            raw["close_reason"] = "PASS all checks"
        if parent:
            raw["parent"] = parent
        (store / f"{bead_id}.json").write_text(json.dumps(raw))
    monkeypatch.setenv("FAKE_BEADS", str(store))
    return store


def test_three_level_chain_is_three_shows(fake_bin, beads: Path) -> None:
    fake_bin.make("bd", SHOW)
    steps = (leaf("atm-bh-4.group-dev", "atm-bh-4.group"), leaf("atm-bh-4.group-qa", "atm-bh-4.group"))
    result = beads_source.ancestors(steps)
    assert isinstance(result, Ok), result
    found = {bead.id: bead for bead in result.value}
    assert set(found) == {*(step.id for step in steps), "atm-bh-4.group", "atm-bh-4", "atm-phase-bh"}
    assert (found["atm-bh-4"].parent, found["atm-bh-4"].status) == ("atm-phase-bh", SprintState.CLOSED)
    assert found["atm-bh-4"].closed_at == datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
    assert found["atm-bh-4"].close_reason == "PASS all checks"
    calls = fake_bin.argv()
    # One show per level; the two steps' shared parent is shown once.
    assert calls == [
        ("bd", "show", "atm-bh-4.group", "--json"),
        ("bd", "show", "atm-bh-4", "--json"),
        ("bd", "show", "atm-phase-bh", "--json"),
    ]
    assert not any("--all" in call or "--status" in call or "closed" in call for call in calls)


def test_each_level_is_one_batched_show(fake_bin, beads: Path) -> None:
    fake_bin.make("bd", SHOW)
    steps = (leaf("atm-bh-4.group-dev", "atm-bh-4.group"), leaf("atm-bh-6.group-dev", "atm-bh-6"))
    assert isinstance(beads_source.ancestors(steps), Ok)
    # atm-phase-bh is atm-bh-6's parent at level 2 and atm-bh-4's at level 3: shown once, at level 2.
    assert fake_bin.argv() == [
        ("bd", "show", "atm-bh-4.group", "atm-bh-6", "--json"),
        ("bd", "show", "atm-bh-4", "atm-phase-bh", "--json"),
    ]


def test_known_parents_are_not_shown(fake_bin, beads: Path) -> None:
    fake_bin.make("bd", SHOW)
    root = leaf("atm-phase-bh", None)
    assert beads_source.ancestors((root, leaf("atm-bh-6", "atm-phase-bh"))) == Ok((root, leaf("atm-bh-6", "atm-phase-bh")))
    assert beads_source.show(()) == Ok(())
    assert fake_bin.argv() == []


def test_missing_parent_is_command_failed(fake_bin, beads: Path) -> None:
    fake_bin.make("bd", SHOW)
    result = beads_source.ancestors((leaf("atm-bh-9.group-dev", "atm-bh-9"),))
    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert result.error.argv == ("bd", "show", "atm-bh-9", "--json")
    assert result.error.code == 1 and "no issue found: atm-bh-9" in result.error.stderr


@pytest.mark.parametrize("reply", ["not json", '[{"title": "no id", "status": "open"}]', '{"id": "x"}'],
                         ids=["bad-json", "missing-key", "not-a-list"])
def test_unparseable_reply_is_parse_failed(fake_bin, reply: str) -> None:
    fake_bin.make("bd", f"printf '%s' '{reply}'\n")
    result = beads_source.ancestors((leaf("atm-bh-4.group-dev", "atm-bh-4"),))
    assert isinstance(result, Err) and isinstance(result.error, ParseFailed)
    assert result.error.kind == "parse_failed"
