from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess

import pytest


SCRIPTS = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("task_close", SCRIPTS / "atm-task-close.py")
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)
EXAMPLES = SCRIPTS.parent / "examples"


class World:
    def __init__(self, root, monkeypatch):
        self.root = root
        self.values = json.loads((EXAMPLES / "task-close-vars-dev.json").read_text())
        self.values.update(primary_checkout=str(root), worktree_path=str(root))
        self.values["carry_forward"] = ""
        (root / ".atm-bd").mkdir()
        (root / ".atm-bd/phase-bj.toml").write_text('lead = "phase-lead"\n')
        (root / ".atm.toml").write_text('[atm]\ndefault_team = "test-team"\n')
        config = root / hook.repo_config.RELATIVE_PATH
        config.parent.mkdir(parents=True)
        config.write_text('bead_prefix: atm\ndev_sanity_member: sanity\nqa_member: quality\n')
        self.path = root / "dispatch.json"
        self.beads = {}
        for name in ("checked_bead", "sanity_bead", "qa_bead"):
            bead = self.values[name]
            self.beads[bead] = dict(id=bead, status="in_progress", assignee="developer", metadata={})
        self.beads[self.values["checked_bead"]]["metadata"]["sanity_bead"] = self.values["sanity_bead"]
        self.beads[self.values["sanity_bead"]]["metadata"]["dev_bead"] = self.values["checked_bead"]
        self.beads["atm-phase-bj.stack"] = json.loads((SCRIPTS / "tests/fixtures/stack-bead.json").read_text())
        self.tasks = {self.values[name]: [self.assignment(1)] for name in ("dev_task_id", "sanity_task_id", "qa_task_id")}
        self.messages, self.calls, self.writes = [], [], []
        self.fail_after = None
        self.github_failure = False
        self.overrides = {}
        monkeypatch.setenv("ATM_IDENTITY", "caller")

    def assignment(self, number):
        return dict(event="assigned", message_id=f"assignment-{number}", seq=number,
                    at=f"2026-10-19T00:{number:02d}:00Z", to_state="assigned")

    def run(self, role="dev", **changes):
        self.values.update(changes)
        self.path.write_text(json.dumps(self.values))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = hook.main(["--role", role, "--vars", str(self.path)], runner=self, clock=lambda: 42)
        assert stdout.getvalue() == stderr.getvalue()
        output = json.loads(stdout.getvalue())
        assert result == int("err" in output)
        return output

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if tuple(argv) in self.overrides:
            code, stdout = self.overrides[tuple(argv)]
            return subprocess.CompletedProcess(argv, code, stdout, "injected failure" if code else "")
        output, write = "", False
        if argv[:2] == ["bd", "show"]:
            output = [self.beads[argv[2]]]
        elif argv[:2] == ["bd", "list"]:
            output = [row for row in self.beads.values() if row["metadata"].get("dev_bead")]
        elif argv[:2] == ["bd", "update"]:
            write = True
            record = self.beads[argv[2]]
            for name in ("status", "assignee", "metadata"):
                if "--" + name in argv:
                    value = argv[argv.index("--" + name) + 1]
                    record[name] = json.loads(value) if name == "metadata" else value
        elif argv[:2] == ["bd", "close"]:
            write = True
            self.beads[argv[2]]["status"] = "closed"
            self.beads[argv[2]]["close_reason"] = argv[argv.index("--reason") + 1]
        elif argv[:3] == ["atm", "task", "events"]:
            output = {"events": self.tasks.get(argv[3], [])}
        elif argv[:3] == ["atm", "task", "close"]:
            write = True
            if "--template" in argv:
                values = json.loads(Path(argv[argv.index("--vars") + 1]).read_text())
                body = hook.sc_render.render(argv[argv.index("--template") + 1], values)
                assert argv[3] in body
            self.tasks[argv[3]].append(dict(event=argv[4], to_state=argv[4]))
        elif argv[:2] == ["atm", "search"]:
            matches = self.messages
            for flag, key in (("--type", "type"), ("--from", "sender"), ("--agent", "recipient")):
                if flag in argv:
                    matches = [item for item in matches if item[key] == argv[argv.index(flag) + 1]]
            for index, token in enumerate(argv):
                if token == "--var":
                    name, value = argv[index + 1].split("=", 1)
                    matches = [item for item in matches if str(item["values"].get(name)) == value]
            if argv[2] != "--type":
                matches = [item for item in matches if argv[2] in item["body"]]
            since = argv[argv.index("--since") + 1]
            output = {"hits": [item for item in matches if item["at"] >= since]}
        elif argv[:2] == ["atm", "send"] or argv[:3] == ["atm", "task", "assign"]:
            write = True
            recipient = argv[3] if argv[1] == "task" else argv[2]
            values, template_type = {}, None
            if "--template" in argv:
                template = Path(argv[argv.index("--template") + 1])
                values = json.loads(Path(argv[argv.index("--vars") + 1]).read_text())
                template_type = hook.header(template)["metadata"]["type"]
                body = hook.sc_render.render(template, values)
            else:
                body = argv[3]
            if argv[1] == "task":
                task = argv[argv.index("--task-id") + 1]
                assert task in body
                self.tasks[task].append(self.assignment(len(self.tasks[task]) + 1))
            self.messages.append(dict(type=template_type, values=values, sender=kwargs["env"]["ATM_IDENTITY"],
                                      recipient=recipient, body=body, at="2026-10-19T23:00:00Z"))
        elif argv[0] == "git":
            if argv[1] == "rev-parse":
                output = self.values["commit"] if argv[2] == "HEAD" else "b" * 40
            elif argv[1] == "ls-remote":
                output = self.values["commit"] + "\trefs/heads/branch"
            elif argv[1:4] == ["remote", "get-url", "origin"]:
                output = "git@github.com:example/repo.git"
            else:
                assert argv[1] in ("status", "fetch", "merge-base"), argv
        elif argv[:2] == ["gh", "api"]:
            if self.github_failure == "raise":
                raise OSError("GitHub unavailable")
            if self.github_failure:
                return subprocess.CompletedProcess(argv, 1, "", "HTTP 429")
            output = [{"number": self.values["pr_number"], "head": {"sha": self.values["commit"]}, "base": {"ref": "deliberately-different"}}]
        else:
            raise AssertionError(argv)
        if write:
            self.writes.append((copy.deepcopy(argv), kwargs["env"]["ATM_IDENTITY"]))
            if len(self.writes) == self.fail_after:
                return subprocess.CompletedProcess(argv, 1, "", "failed after committed effect")
        return subprocess.CompletedProcess(argv, 0, output if isinstance(output, str) else json.dumps(output), "")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def test_dev_handoff_is_immediate_and_does_not_close_task(world, monkeypatch):
    before = dict(hook.os.environ)
    assert "ok" in world.run()
    assert dict(hook.os.environ) == before
    calls = [argv for argv, _kwargs in world.calls]
    close_index = next(index for index, argv in enumerate(calls) if argv[:2] == ["bd", "close"])
    assert calls[close_index + 1][:3] == ["atm", "task", "assign"]
    assert world.tasks[world.values["dev_task_id"]][-1]["to_state"] == "assigned"
    for argv, sender in world.writes:
        assert sender == ("phase-lead" if argv[:3] == ["atm", "task", "assign"] else "caller")
        if argv[0] == "bd":
            assert argv[-2:] == ["--actor", "caller"]
        if "--vars" in argv:
            assert argv[argv.index("--vars") + 1] == str(world.path)
    for argv, kwargs in world.calls:
        if argv[:3] == ["atm", "task", "assign"]:
            assert kwargs["env"]["ATM_TEAM"] == "test-team"
        else:
            assert kwargs["env"].get("ATM_TEAM") == before.get("ATM_TEAM")


def test_preview_and_native_send_use_identical_unmodified_vars(world, monkeypatch):
    world.values.pop("carry_forward")
    original_render = hook.sc_render.render
    inputs = []

    def render(template, values):
        inputs.append(copy.deepcopy(values))
        return original_render(template, values)

    monkeypatch.setattr(hook.sc_render, "render", render)
    assert "ok" in world.run()
    assert inputs and all(values == world.values for values in inputs)
    assert all(message["values"] == world.values for message in world.messages)


@pytest.mark.parametrize("change,code", [({"commit": ""}, "VARS.EMPTY"), ({"extra": 1}, "VARS.UNKNOWN"),
                                      ({"verdict": "rebase_required"}, "VARS.INVALID")])
def test_invalid_vars_have_no_commands(world, change, code):
    assert world.run("sanity", **change)["err"]["code"] == code
    assert world.calls == []


@pytest.mark.parametrize("argv", [[], ["--role", "other", "--vars", "/absent"], ["--vars", "/absent"]])
def test_argument_errors_use_typed_json(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert hook.main(argv, runner=lambda *_args, **_kwargs: pytest.fail("unexpected command")) == 1
    assert json.loads(stdout.getvalue())["err"]["code"] == "ARGS.INVALID"
    assert stdout.getvalue() == stderr.getvalue()


def test_every_required_field_is_validated_before_commands(world):
    original = dict(world.values)
    for field in hook.header(hook.TEMPLATES / "task-close-vars.j2")["required_variables"]:
        world.values = dict(original)
        del world.values[field]
        assert world.run()["err"]["code"] == "VARS.MISSING", field
    assert world.calls == []


@pytest.mark.parametrize("failure", [True, "raise"])
def test_github_retries_and_falls_back_without_requiring_pr_base(world, failure):
    world.github_failure = failure
    assert world.run()["ok"]["github"] == "unavailable"
    assert len([argv for argv, _kwargs in world.calls if argv[0] == "gh"]) == 3


def test_ledger_top_overrides_static_target(world):
    from bead_schema import StackMetadata

    metadata = world.beads["atm-phase-bj.stack"]["metadata"]
    metadata["layers"] = [
        {"branch": "irrelevant-broken-lower-layer", "pr": 350, "head": "0" * 40,
         "base": metadata["trunk"], "linked": True},
        {"branch": "new-top", "pr": 351, "head": "b" * 40,
         "base": "irrelevant-broken-lower-layer", "linked": True},
    ]
    validated = StackMetadata.model_validate(metadata)
    assert len(validated.layers) == 2
    assert validated.layers[-1].branch == "new-top"
    assert "ok" in world.run()
    calls = [argv for argv, _kwargs in world.calls]
    assert ["git", "merge-base", "--is-ancestor", "origin/new-top", world.values["commit"]] in calls
    assert not any("irrelevant-broken-lower-layer" in str(argv) for argv in calls)


def test_sanity_lookup_fallback_and_actionable_missing_bead(world):
    del world.beads[world.values["checked_bead"]]["metadata"]["sanity_bead"]
    assert "ok" in world.run()
    world.beads[world.values["sanity_bead"]]["metadata"] = {}
    world.writes.clear()
    output = world.run()
    assert output["err"]["code"] == "BEAD.NO_SANITY"
    assert "phase-lead" in output["err"]["fix"]
    assert world.writes == []


@pytest.mark.parametrize("argv,response,code", [
    (["git", "rev-parse", "HEAD"], (0, "wrong"), "HEAD.NOT_PUSHED"),
    (["git", "status", "--porcelain"], (0, " M source.py"), "HEAD.DIRTY"),
    (["git", "ls-remote", "origin", "refs/heads/sprint/bj-1-task-close-hook"], (0, "wrong"), "HEAD.NOT_PUSHED"),
    (["git", "merge-base", "--is-ancestor", "origin/integrate/phase-bj", "a" * 40], (1, ""), "BASE.NOT_ANCESTOR"),
    (["gh", "api", "repos/example/repo/pulls?head=example:sprint/bj-1-task-close-hook&state=open"], (0, "[]"), "PR.NOT_FOUND"),
])
def test_dev_preconditions_do_not_write(world, argv, response, code):
    world.overrides[tuple(argv)] = response
    assert world.run()["err"]["code"] == code
    assert world.writes == []


def test_not_reproducible_assigns_runtime_top_without_rewriting_input(world):
    for field in ("branch", "commit", "pr_number"):
        del world.values[field]
    result = world.run("fix", outcome="not_reproducible")
    assert "ok" in result, result
    assert json.loads(world.path.read_text()) == world.values
    assignment = next(message for message in world.messages if message["type"] == "dev-sanity-task")
    assert assignment["values"]["branch"] == "integrate/phase-bj"
    assert assignment["values"]["commit"] == "b" * 40
    assert set(assignment["values"]) == set(world.values) | {"branch", "commit"}
    assert not any(argv[0] == "gh" for argv, _kwargs in world.calls)


def test_sanity_pass_closes_dev_as_lead_then_assigns_qa(world):
    assert "ok" in world.run("sanity")
    assert world.beads[world.values["sanity_bead"]]["status"] == "closed"
    dev_close = next((argv, sender) for argv, sender in world.writes if argv[:4] == ["atm", "task", "close", world.values["dev_task_id"]])
    assert dev_close[1] == "phase-lead"
    assert world.messages[-1]["recipient"] == "quality"


def test_sanity_requires_stack_except_not_reproducible(world):
    assert world.run("sanity", stacked=None)["err"]["code"] == "SANITY.NOT_STACKED"
    assert world.writes == []
    assert "ok" in world.run("sanity", outcome="not_reproducible")


@pytest.mark.parametrize("cap", [2, 5])
def test_failure_cap_exceeds_limit_and_counts_distinct_assignments(world, cap):
    config = world.root / hook.repo_config.RELATIVE_PATH
    config.write_text(config.read_text() + f"max_sanity_rounds: {cap}\n")
    bead = world.values["sanity_bead"]
    checked = world.values["checked_bead"]
    for number in range(1, cap + 2):
        world.tasks[bead] = [world.assignment(number)]
        world.beads[bead].update(status="in_progress", assignee="sanity")
        world.beads[checked]["status"] = "closed"
        output = world.run("sanity", verdict="FAIL", undone=[{"number": 2, "reason": "verbatim missing implementation"}])
        assert "ok" in output, output
        assert world.beads[bead]["metadata"]["sanity_round"] == number
        if number <= cap:
            assert world.beads[checked]["status"] == "open"
            assert world.beads[bead]["assignee"] == ""
        else:
            assert output["ok"]["escalated"] == "SANITY.ROUND_CAP"
            assert world.beads[checked]["status"] == "closed"
            assert world.beads[bead]["status"] == "in_progress"


def test_cannot_run_resets_sanity_and_refuses_without_notice(world):
    assert "ok" in world.run("sanity", verdict="CANNOT_RUN")
    assert world.beads[world.values["sanity_bead"]]["status"] == "open"
    assert world.tasks[world.values["sanity_bead"]][-1]["to_state"] == "refused"
    assert world.messages == []


@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
def test_qa_closes_bead_before_task_and_sends_lead_status(world, verdict):
    assert "ok" in world.run("qa", verdict=verdict)
    assert [argv[:2] for argv, _sender in world.writes] == [["bd", "update"], ["bd", "close"], ["atm", "task"], ["atm", "send"]]
    assert world.messages[0]["recipient"] == "phase-lead"
    assert world.messages[0]["type"] is None
    assert world.messages[0]["body"] == f"QA {verdict} {world.values['qa_bead']} commit={world.values['commit']} round=1"
    assert "--template" not in world.writes[-1][0]


def test_close_key_is_triple_and_only_fail_counter_key_adds_assignment(world):
    assert "ok" in world.run("sanity", verdict="FAIL", undone=[{"number": 1, "reason": "not implemented"}])
    bead = world.values["sanity_bead"]
    metadata = world.beads[bead]["metadata"]
    assert metadata["task_close_key"] == [bead, "FAIL", world.values["commit"]]
    assert metadata["sanity_round_key"] == [bead, "assignment-1", "FAIL", world.values["commit"]]
    world.tasks[bead] = [world.assignment(2)]
    assert "ok" in world.run("sanity", verdict="PASS")
    assert world.beads[bead]["metadata"]["task_close_key"] == [bead, "PASS", world.values["commit"]]


def test_existing_four_part_state_allows_a_new_assignment(world):
    bead = world.values["sanity_bead"]
    world.beads[bead]["metadata"]["task_close_key"] = [bead, "assignment-0", "FAIL", "old-head"]
    assert "ok" in world.run("sanity")
    assert world.beads[bead]["metadata"]["task_close_key"] == [bead, "PASS", world.values["commit"]]


@pytest.mark.parametrize("role,verdict", [("dev", "PASS"), ("fix", "PASS"), ("sanity", "PASS"), ("sanity", "FAIL"), ("sanity", "CANNOT_RUN"), ("qa", "PASS"), ("qa", "FAIL")])
def test_replay_after_every_committed_write_converges(tmp_path, monkeypatch, role, verdict):
    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    baseline = World(baseline_root, monkeypatch)
    changes = dict(verdict=verdict, undone=[{"number": 1, "reason": "not implemented"}])
    assert "ok" in baseline.run(role, **changes)
    for boundary in range(1, len(baseline.writes) + 1):
        root = tmp_path / f"boundary-{boundary}"
        root.mkdir()
        world = World(root, monkeypatch)
        world.fail_after = boundary
        assert "err" in world.run(role, **changes)
        world.fail_after = None
        result = world.run(role)
        assert "ok" in result, (boundary, result)
        assert world.beads == baseline.beads
        for task in baseline.tasks:
            assert world.tasks[task] == baseline.tasks[task]
        assert [(item["type"], item["recipient"]) for item in world.messages] == [(item["type"], item["recipient"]) for item in baseline.messages]


def test_replay_with_changed_verdict_or_head_is_conflict(world):
    assert "ok" in world.run("sanity")
    assert world.run("sanity", verdict="FAIL", undone=[{"number": 1, "reason": "different"}])["err"]["code"] == "REPLAY.CONFLICT"
    assert world.run("sanity", verdict="PASS", commit="c" * 40)["err"]["code"] == "REPLAY.CONFLICT"


def test_fix_pass_requires_its_own_carry_forward(world):
    world.beads[world.values["checked_bead"]]["labels"] = ["stage:fix"]
    assert world.run("sanity")["err"]["code"] == "VARS.INVALID"
    assert world.writes == []
    assert "ok" in world.run("sanity", carry_forward=world.values["checked_bead"])
    assert world.messages[-1]["values"]["carry_forward"] == world.values["checked_bead"]


def test_cannot_run_replay_rejects_changed_verdict(world):
    assert "ok" in world.run("sanity", verdict="CANNOT_RUN")
    assert world.run("sanity", verdict="PASS")["err"]["code"] == "REPLAY.CONFLICT"


def test_round_cap_replay_sends_escalation_once(world):
    bead = world.values["sanity_bead"]
    world.beads[bead]["metadata"]["sanity_round"] = 5
    world.beads[world.values["checked_bead"]]["status"] = "closed"
    result = world.run("sanity", verdict="FAIL", undone=[{"number": 7, "reason": "not implemented"}])
    assert result["ok"]["escalated"] == "SANITY.ROUND_CAP"
    assert world.run("sanity")["ok"]["escalated"] == "SANITY.ROUND_CAP"
    assert len(world.messages) == 1
    assert world.beads[bead]["metadata"]["sanity_round"] == 6


def test_same_head_dev_retry_reassigns_sanity_after_fail(world):
    assert "ok" in world.run()
    assert "ok" in world.run("sanity", verdict="FAIL", undone=[{"number": 1, "reason": "missing evidence"}])
    assert "ok" in world.run("dev", verdict="PASS", undone=[])
    sanity = world.values["sanity_bead"]
    assert world.tasks[sanity][-1]["to_state"] == "assigned"
    assert len([item for item in world.messages if item["type"] == "dev-sanity-task"]) == 2
    assert "ok" in world.run("dev")
    assert len([item for item in world.messages if item["type"] == "dev-sanity-task"]) == 2


def test_dev_replay_does_not_bypass_round_cap(world):
    assert "ok" in world.run()
    sanity = world.values["sanity_bead"]
    world.beads[sanity]["metadata"]["sanity_round"] = 5
    assert world.run("sanity", verdict="FAIL", undone=[{"number": 1, "reason": "not implemented"}])["ok"]["escalated"] == "SANITY.ROUND_CAP"
    assert "ok" in world.run("dev", verdict="PASS", undone=[])
    assert world.tasks[sanity][-1]["to_state"] == "completed"
    assert len([item for item in world.messages if item["type"] == "dev-sanity-task"]) == 1


@pytest.mark.parametrize("boundary", [1, 2, 3, 4])
def test_not_reproducible_replay_pins_first_handoff_when_top_moves(world, boundary):
    for field in ("branch", "commit", "pr_number"):
        del world.values[field]
    world.fail_after = boundary
    assert "err" in world.run("fix", outcome="not_reproducible")
    world.overrides[("git", "rev-parse", "origin/integrate/phase-bj")] = (0, "c" * 40)
    world.fail_after = None
    assert "ok" in world.run("fix")
    assignments = [item for item in world.messages if item["type"] == "dev-sanity-task"]
    assert len(assignments) == 1
    assert assignments[0]["values"]["commit"] == "b" * 40
    assert len([item for item in world.messages if item["type"] == "fix-complete"]) == 1


def test_shared_contract_covers_all_role_templates_and_ids(world):
    contract = hook.header(hook.TEMPLATES / "task-close-vars.j2")
    names = ("dev-complete.md.j2", "fix-complete.md.j2", "dev-sanity-template.xml.j2", "sanity-fail.xml.j2",
             "dev-sanity-complete.md.j2", "qa-template.xml.j2", "qa-complete.md.j2")
    for name in names:
        assert set(hook.header(hook.TEMPLATES / name)["required_variables"]) <= set(contract["required_variables"]), name
        body = hook.sc_render.render(hook.TEMPLATES / name, world.values)
        role_id = "sanity_task_id" if name.startswith("dev-sanity") else "qa_task_id" if name.startswith("qa-") else "dev_task_id"
        if name != "sanity-fail.xml.j2":
            assert world.values[role_id] in body
    assert hook.sc_render.render(hook.TEMPLATES / "task-close-vars.j2", world.values).strip() == ""
    assert "push" in hook.sc_render.render(hook.TEMPLATES / "rebase-required.xml.j2", world.values).lower()


@pytest.mark.parametrize("role", ["dev", "fix", "sanity", "qa"])
def test_each_role_example_renders_every_shared_template(role):
    values = json.loads((EXAMPLES / f"task-close-vars-{role}.json").read_text())
    hook.validate(values, role)
    for name in ("task-close-vars.j2", "dev-complete.md.j2", "fix-complete.md.j2", "dev-sanity-template.xml.j2",
                 "dev-sanity-complete.md.j2", "sanity-fail.xml.j2", "qa-template.xml.j2", "qa-complete.md.j2"):
        hook.sc_render.render(hook.TEMPLATES / name, values)


def test_help_describes_the_permission_only_override(capsys):
    with pytest.raises(SystemExit) as exit_info:
        hook.main(["--help"])
    assert exit_info.value.code == 0
    assert " ".join("Do NOT use without permission from the phase lead.".split()) in " ".join(capsys.readouterr().out.split())


def d1_run_path(world, path):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = hook.main(["--role", "dev", "--vars", str(path)], runner=world, clock=lambda: 42)
    assert result == 1
    assert stdout.getvalue() == stderr.getvalue()
    return json.loads(stdout.getvalue())["err"]


@pytest.mark.parametrize("contents", [None, '{"broken":', '[]', 'null'])
def test_d1_vars_read_errors_name_exact_path_and_correction(world, contents):
    path = world.root / "bad-input.json"
    if contents is not None:
        path.write_text(contents)
    error = d1_run_path(world, path)
    assert error["code"] == "VARS.INVALID"
    assert str(path) in error["message"]
    assert str(path) in error["fix"]
    assert world.calls == []


@pytest.mark.parametrize("relative,field,contents", [
    (".atm-bd/phase-bj.toml", "lead", None),
    (".atm-bd/phase-bj.toml", "lead", "lead = ["),
    (".atm-bd/phase-bj.toml", "lead", "other = 'value'\n"),
    (".atm-bd/phase-bj.toml", "lead", "lead = 5\n"),
    (".atm.toml", "atm.default_team", None),
    (".atm.toml", "atm.default_team", "[atm\n"),
    (".atm.toml", "atm.default_team", "[atm]\n"),
    (".atm.toml", "atm.default_team", "[atm]\ndefault_team = ''\n"),
])
def test_d1_toml_errors_name_file_and_field_before_commands(world, relative, field, contents):
    path = world.root / relative
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    error = world.run()["err"]
    assert error["code"] == "CONFIG.INVALID"
    assert str(path) in error["message"] and field in error["message"]
    assert str(path) in error["fix"] and field in error["fix"]
    assert world.calls == []


@pytest.mark.parametrize("contents", [None, "invalid: [", "[]", "max_sanity_rounds: zero\n"])
def test_d1_repository_config_errors_name_generated_path(world, contents):
    path = world.root / hook.repo_config.RELATIVE_PATH
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    error = world.run()["err"]
    assert error["code"] == "CONFIG.INVALID"
    assert str(path) in error["message"] and str(path) in error["fix"]
    assert world.calls == []


@pytest.mark.parametrize("field", ["bead_prefix", "dev_sanity_member"])
def test_d1_missing_repository_field_names_correction(world, field):
    path = world.root / hook.repo_config.RELATIVE_PATH
    values = hook.yaml.safe_load(path.read_text())
    del values[field]
    path.write_text(hook.yaml.safe_dump(values))
    error = world.run()["err"]
    assert error["code"] == "CONFIG.INVALID"
    assert field in error["message"] and field in error["fix"]
    assert str(path) in error["fix"]
    assert world.calls == []


@pytest.mark.parametrize("count", ["not-an-integer", "2", -1, True, None, 1.5, [], {}])
def test_d1_invalid_sanity_counter_names_bead_metadata_and_fix(world, count):
    bead = world.values["sanity_bead"]
    world.beads[bead]["metadata"]["sanity_round"] = count
    error = world.run("sanity", verdict="FAIL", undone=[{"number": 1, "reason": "missing"}])["err"]
    assert error["code"] == "BEAD.INVALID"
    assert bead in error["message"] and "metadata.sanity_round" in error["message"]
    assert bead in error["fix"] and "metadata.sanity_round" in error["fix"]
    assert world.writes == []


@pytest.mark.parametrize("field", ["primary_checkout", "worktree_path", "phase"])
def test_d1_invalid_path_fields_name_the_required_string(world, field):
    error = world.run(**{field: 42})["err"]
    assert error["code"] == "VARS.INVALID"
    assert field in error["message"] and field in error["fix"]
    assert world.calls == []


@pytest.mark.parametrize("metadata,field", [
    ([], "$[0].metadata"),
    ({"layers": {}}, "$[0].metadata.layers"),
    ({"layers": ["invalid"]}, "$[0].metadata.layers[0]"),
    ({"layers": [{"branch": "top"}]}, "$[0].metadata.layers[0].head"),
])
def test_d1_malformed_bead_shapes_name_producer_and_field(world, metadata, field):
    bead = "atm-phase-bj.stack"
    world.beads[bead]["metadata"] = metadata
    error = world.run()["err"]
    producer = f"bd show {bead} --json"
    assert error["code"] == "COMMAND.INVALID"
    assert producer in error["message"] and field in error["message"]
    assert producer in error["fix"] and field in error["fix"]
    assert "vars file and" not in error["fix"]
    assert world.writes == []


@pytest.mark.parametrize("payload,field", [
    ([], "$"),
    ({"events": {}}, "$.events"),
    ({"events": [42]}, "$.events[0]"),
    ({"events": [{"event": "assigned", "seq": 1}]}, "$.events[0].at"),
    ({"events": [{"event": "assigned", "at": "2026-10-19T00:00:00Z"}]}, "$.events[0].message_id/seq"),
])
def test_d1_malformed_atm_events_name_producer_and_field(world, payload, field):
    argv = ("atm", "task", "events", world.values["dev_task_id"], "--all", "--json")
    world.overrides[argv] = (0, json.dumps(payload))
    error = world.run()["err"]
    producer = " ".join(argv)
    assert error["code"] == "COMMAND.INVALID"
    assert producer in error["message"] and field in error["message"]
    assert producer in error["fix"] and field in error["fix"]
    assert world.writes == []
