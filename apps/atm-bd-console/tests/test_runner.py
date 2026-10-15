from __future__ import annotations

from dataclasses import replace

from contract_samples import BEAD, CONFIG, PHASE_FILE, REPO_CONFIG, STATE

from atm_bd_console import runner
from atm_bd_console.result import Ok


def test_closed_root_stops_before_any_other_source_read(monkeypatch) -> None:
    closed = replace(BEAD, id="atm-phase-bh", labels=("phase:bh",), status="closed")
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok((closed,)))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closed root must not read another source")

    monkeypatch.setattr(runner.beads_source, "open_beads", forbidden)
    monkeypatch.setattr(runner.atm_source, "members", forbidden)
    snapshot, _ = runner.step(CONFIG, STATE, False)
    assert snapshot.root_closed and snapshot.stopped == "closed"


def test_github_off_never_calls_github(monkeypatch) -> None:
    monkeypatch.setattr(runner.github_source, "open_prs", lambda *_: (_ for _ in ()).throw(AssertionError("github")))
    # A malformed phase prevents due scheduling and is sufficient to exercise
    # the no-GitHub path without invoking real boundary commands.
    monkeypatch.setattr(runner.atm_bd, "phase_file", lambda *_: Ok(PHASE_FILE))
    monkeypatch.setattr(runner.atm_bd, "repo_config", lambda *_: Ok(REPO_CONFIG))
    monkeypatch.setattr(runner.beads_source, "show", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "open_beads", lambda *_: Ok(()))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "members", lambda *_: Ok(()))
    monkeypatch.setattr(runner.atm_source, "tasks", lambda *_: Ok(()))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok(()))
    runner.step(replace(CONFIG, github="off"), STATE, True)
