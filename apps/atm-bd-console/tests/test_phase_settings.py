from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from contract_samples import BEAD, MERGE, PHASE_TEMPLATES, REMOTE_REF, SPRINT, WORKTREE

from atm_bd_console import atm_bd, cli, config, runner
from atm_bd_console.enums import MergeState, SprintState
from atm_bd_console.result import ConfigInvalid, Err, Ok
from atm_bd_console.state import acquire_lock, load_snapshot
from atm_bd_console.types import INITIAL_STATE


def set_setting(root: Path, key: str, value: object, *, absent: bool = False) -> None:
    path = root / ".atm-bd/phase-bh.toml"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(
        line if not line.startswith(f"{key} = ") else f"{key} = {json.dumps(value)}"
        for line in lines if not (absent and line.startswith(f"{key} = "))
    ) + "\n")


@pytest.mark.parametrize("key", ("lead", *PHASE_TEMPLATES))
@pytest.mark.parametrize("damage", ("absent", "empty", "whitespace", "non_string"))
def test_invalid_phase_setting_fails_launch_before_effects(phase_repo, monkeypatch, capsys, key, damage):
    values = {"absent": None, "empty": "", "whitespace": "  ", "non_string": 17}
    set_setting(phase_repo, key, values[damage], absent=damage == "absent")
    monkeypatch.setenv("ATM_IDENTITY", "environment-lead")
    with (phase_repo / ".claude/project/atm-bd-orchestration.yaml").open("a") as stream:
        stream.write('\nlead: "repository-lead"\n')
    expected = key if key == "lead" else f"templates.{key}"
    loaded = config.load_config(phase_repo, "bh")
    assert isinstance(loaded, Err) and isinstance(loaded.error, ConfigInvalid)
    assert loaded.error.key == expected

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid phase settings must fail before runtime effects")

    monkeypatch.setattr(cli.config_source, "repo_root", lambda: phase_repo)
    for source, name in ((cli, "acquire_lock"), (cli.runner, "step"), (runner.atm_source, "assign"),
                         (runner.atm_source, "send"), (cli.shutil, "which")):
        monkeypatch.setattr(source, name, forbidden)
    assert cli.main(["cron", "--phase", "bh"]) == 3
    assert expected in capsys.readouterr().out
    assert not (phase_repo / ".atm-bd/atm-bd").exists()


@pytest.mark.parametrize("purpose", tuple(PHASE_TEMPLATES))
@pytest.mark.parametrize("damage", (
    "missing_file", "absolute", "outside", "traversal", "directory", "symlink", "trailing_slash", "trailing_dot",
))
def test_template_paths_must_name_existing_installed_files(phase_repo, tmp_path, monkeypatch, capsys, purpose, damage):
    directory = Path(".claude/skills/atm-bd-orchestration/templates")
    outside = tmp_path / "outside.xml.j2"
    outside.write_text("outside")
    link = phase_repo / directory / "escape.xml.j2"
    link.symlink_to(outside)
    paths = {
        "missing_file": str(directory / "missing.xml.j2"),
        "absolute": str(phase_repo / PHASE_TEMPLATES[purpose]),
        "outside": "docs/plans/phase-bh.jsonl",
        "traversal": str(directory / ".." / "templates" / Path(PHASE_TEMPLATES[purpose]).name),
        "directory": str(directory), "symlink": str(directory / link.name),
        "trailing_slash": PHASE_TEMPLATES[purpose] + "/",
        "trailing_dot": PHASE_TEMPLATES[purpose] + "/.",
    }
    set_setting(phase_repo, purpose, paths[damage])
    result = atm_bd.phase_file(str(phase_repo), "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == f"templates.{purpose}"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid template paths must fail before runtime effects")

    monkeypatch.setattr(cli.config_source, "repo_root", lambda: phase_repo)
    for source, name in ((cli, "acquire_lock"), (runner.atm_source, "assign"), (runner.atm_source, "send")):
        monkeypatch.setattr(source, name, forbidden)
    assert cli.main(["cron", "--phase", "bh"]) == 3
    assert f"templates.{purpose}" in capsys.readouterr().out
    assert not (phase_repo / ".atm-bd/atm-bd").exists()


@pytest.mark.parametrize("purpose", ("dev_assign", "sanity_assign", "qa_assign", "fix_review", "unknown"))
def test_unrecognized_template_purposes_are_rejected(phase_repo, purpose):
    with (phase_repo / ".atm-bd/phase-bh.toml").open("a") as stream:
        stream.write(f'{purpose} = "{PHASE_TEMPLATES["qa"]}"\n')
    result = atm_bd.phase_file(str(phase_repo), "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == f"templates.{purpose}"


def test_missing_templates_table_names_first_required_purpose(phase_repo):
    path = phase_repo / ".atm-bd/phase-bh.toml"
    path.write_text(path.read_text().split("[templates]")[0])
    result = atm_bd.phase_file(str(phase_repo), "bh")
    assert isinstance(result, Err) and isinstance(result.error, ConfigInvalid)
    assert result.error.key == "templates.dev"


@pytest.mark.parametrize("kind", ("sanity", "qa"))
def test_real_assignment_receives_phase_lead_and_configured_path(phase_repo, monkeypatch, fake_bin, capsys, kind):
    template = f".claude/skills/atm-bd-orchestration/templates/custom-{kind}.xml.j2"
    (phase_repo / template).write_text("custom assignment")
    set_setting(phase_repo, kind, template)
    set_setting(phase_repo, "lead", "phase-owner")
    (phase_repo / "docs/plans/phase-bh.jsonl").write_text('{"sprint": "bh-6"}\n')
    monkeypatch.setenv("ATM_IDENTITY", "environment-owner")
    with (phase_repo / ".claude/project/atm-bd-orchestration.yaml").open("a") as stream:
        stream.write('\nlead: "repository-owner"\n')
    root = replace(BEAD, id="atm-phase-bh", status=SprintState.OPEN, parent=None, labels=("phase-bh",), metadata={})
    sprint = replace(BEAD, id=SPRINT.id, status=SprintState.OPEN, parent=root.id, labels=("stage:sprint",), metadata={
        "layer": SPRINT.layer, "branch": SPRINT.branch, "pr_target": SPRINT.target, "stack": SPRINT.stack,
    })
    candidate = replace(BEAD, id=f"{SPRINT.id}.group-{kind}", status=SprintState.OPEN, parent=sprint.id, assignee=None,
                        labels=("stage:dev-sanity" if kind == "sanity" else "stage:qa",), metadata={
        "sprint": "bh-6", "sprint_bead": sprint.id, "dev_bead": f"{sprint.id}.group-dev",
        "checked_bead": f"{sprint.id}.group-dev", "round": 1, "branch": SPRINT.branch,
        "pr_target": SPRINT.target, "worktree": WORKTREE.path,
    })
    beads = {bead.id: bead for bead in (root, sprint, candidate)}
    monkeypatch.setattr(cli.config_source, "repo_root", lambda: phase_repo)
    monkeypatch.chdir(phase_repo)
    monkeypatch.setattr(runner.beads_source, "show", lambda ids: Ok(tuple(beads[item] for item in ids if item in beads)))
    monkeypatch.setattr(runner.beads_source, "open_rows", lambda *_: Ok(runner.beads_source.BeadRows((candidate,), ())))
    monkeypatch.setattr(runner.beads_source, "ready", lambda *_: Ok((candidate,)))
    monkeypatch.setattr(runner.git_source, "worktrees", lambda *_: Ok((WORKTREE,)))
    monkeypatch.setattr(runner.git_source, "fetch", lambda *_: Ok(None))
    monkeypatch.setattr(runner.git_source, "remote_refs", lambda *_: Ok((REMOTE_REF,)))
    monkeypatch.setattr(runner.git_source, "merge", lambda *_: Ok(replace(MERGE, state=MergeState.CLEAN, files=(), messages=())))
    fake_bin.make("bd", "exit 97\n")
    fake_bin.make("git", "exit 97\n")
    fake_bin.make("atm", '''case "$1:$2" in
members:--team) printf '{"members": []}';;
task:list) printf '[]';;
task:assign) exit 0;;
*) exit 97;;
esac
''')
    assert cli.main(["cron", "--phase", "bh", "--github", "off"]) == 0
    snapshot = load_snapshot(phase_repo, root.id)
    assert isinstance(snapshot, Ok) and snapshot.value.errors == ()
    assigned = [argv for argv in fake_bin.argv() if argv[1:3] == ("task", "assign")]
    recipient = "atm-sanity" if kind == "sanity" else "quality-mgr"
    assert len(assigned) == 1
    assert assigned[0][:-1] == (
        "atm", "task", "assign", recipient, "--as", "phase-owner", "--team", "atm-dev",
        "--task-id", candidate.id, "--template", template, "--vars",
    )
    assert not Path(assigned[0][-1]).exists()
    assert json.loads(capsys.readouterr().out)["outcome"] == "sent"
    assert snapshot.value.lead == "phase-owner"

    loaded = config.load_config(phase_repo, "bh", github="off")
    assert isinstance(loaded, Ok) and loaded.value.lead == "phase-owner"
    set_setting(phase_repo, "lead", "updated-phase-owner")
    updated_template = f".claude/skills/atm-bd-orchestration/templates/updated-{kind}.xml.j2"
    (phase_repo / updated_template).write_text("updated assignment")
    set_setting(phase_repo, kind, updated_template)
    with acquire_lock(phase_repo, root.id).value as store:
        refreshed, _ = runner.step(loaded.value, INITIAL_STATE, False, store)
    assert refreshed.lead == "updated-phase-owner"
    assigned = [argv for argv in fake_bin.argv() if argv[1:3] == ("task", "assign")]
    assert len(assigned) == 2
    assert assigned[-1][5] == "updated-phase-owner"
    assert assigned[-1][11] == updated_template

    set_setting(phase_repo, "lead", None, absent=True)
    before = fake_bin.argv()
    with acquire_lock(phase_repo, root.id).value as store:
        invalid, _ = runner.step(loaded.value, INITIAL_STATE, False, store)
    assert any(isinstance(error, ConfigInvalid) and error.key == "lead" for error in invalid.errors)
    assert fake_bin.argv() == before
