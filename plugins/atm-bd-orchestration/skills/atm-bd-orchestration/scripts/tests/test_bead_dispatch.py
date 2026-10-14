"""bead-dispatch against a real bd workspace (the pour tests' own dolt server), with `atm` and `gh` stubbed.

Sprint and finding groups are poured by bead-groups, so every sanity and QA
bead carries the `branch` its pour wrote. Only `atm` and `gh` are fake
executables on PATH: `atm` prints the task list from a file and logs every
assign, send and escalation call; `gh` prints the open PR of a branch, the
stacks and the check runs from files the test writes, and logs every call.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bead_pour_mock import MISSING, SKIP_REASON, Workspace, findings_file, fix_group, group, init_workspace, stop_server  # noqa: E402

FAKE_ATM = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
log = os.environ["FAKE_DIR"]
if argv[:2] == ["task", "list"]:
    sys.stdout.write(open(os.path.join(log, "tasks.json")).read())
elif argv[:2] == ["task", "assign"]:
    vars_file = argv[argv.index("--vars") + 1]
    with open(os.path.join(log, "assigns.jsonl"), "a") as fh:
        fh.write(json.dumps({"argv": argv, "vars": json.load(open(vars_file))}) + "\\n")
elif argv[:2] == ["escalation", "list"]:
    print(json.dumps({"recipients": ["omega"]}))
elif argv[:1] == ["send"]:
    with open(os.path.join(log, "sends.jsonl"), "a") as fh:
        fh.write(json.dumps({"to": argv[1], "body": sys.stdin.read()}) + "\\n")
else:
    sys.exit("fake atm: unexpected " + " ".join(argv))
"""
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
endpoint = next(a for a in sys.argv[2:] if a.startswith("repos/"))
with open(os.path.join(d, "gh.log"), "a") as log:
    log.write(endpoint + "\\n")
if "/pulls?head=" in endpoint:
    branch = endpoint.split("head={owner}:", 1)[1].split("&", 1)[0]
    print(json.dumps(json.load(open(os.path.join(d, "pulls.json"))).get(branch, [])))
elif "/stacks" in endpoint:
    print(json.dumps([json.load(open(os.path.join(d, "stacks.json")))]))
elif "/check-runs" in endpoint:
    print(json.dumps(json.load(open(os.path.join(d, "runs.json")))))
else:
    sys.exit("fake gh: unexpected " + endpoint)
"""
CONFIG = ('lead: lead1\nworktree_base: wt\nlint_command: "make lint"\npolicy_path: policy.md\n'
          'reviewers_round1: ["req-qa", "arch-qa"]\n')


def pr(number: int, branch: str, base: str, sha: str = "a" * 40) -> dict:
    return {"number": number, "html_url": f"https://x/pull/{number}", "head": {"ref": branch, "sha": sha}, "base": {"ref": base}}


def stack(number: int, *prs: int) -> dict:
    return {"number": number, "open": True, "pull_requests": [{"number": n, "state": "open"} for n in prs]}


@unittest.skipUnless(not MISSING, SKIP_REASON)
class BeadDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(tempfile.mkdtemp(prefix="bead-dispatch-")).resolve()
        cls.ws = Workspace(root)
        cls.addClassCleanup(shutil.rmtree, root, ignore_errors=True)
        cls.addClassCleanup(stop_server, root)
        init_workspace(cls.ws)
        config = root / ".claude/project/atm-bd-orchestration.yaml"
        config.write_text(config.read_text() + CONFIG)
        bins = root / "fake-bin"
        bins.mkdir()
        for name, body in (("atm", FAKE_ATM), ("gh", FAKE_GH)):
            (bins / name).write_text(body)
            (bins / name).chmod(0o755)

    def phase(self, name: str) -> tuple[str, Path]:
        """A phase root, one poured sprint (branch sprint/<name>-1) and its worktree; returns (sprint, fake dir)."""
        ws = self.ws
        root = f"t-phase-{name}"
        sprint = ws.sprint(name, 1)
        ws.bd("create", f"phase {name}", "--id", root, "--type", "epic", "-l", f"phase-{name}", "--silent")
        ws.bd("update", sprint, "--parent", root)
        ws.groups("--sprint", sprint)
        (ws.root / "wt" / f"sprint/{name}-1").mkdir(parents=True)
        fake = ws.root / f"fake-{name}"
        fake.mkdir()
        self.write(fake, tasks=[], pulls={}, stacks=[], runs=[])
        return sprint, fake

    @staticmethod
    def write(fake: Path, **files) -> None:
        for name, value in files.items():
            (fake / f"{name}.json").write_text(json.dumps(value))

    def dispatch(self, phase: str, fake: Path, *args: str, expect: int = 0) -> list[dict]:
        env = {**self.ws.env, "PATH": f"{self.ws.root / 'fake-bin'}{os.pathsep}{self.ws.env['PATH']}",
               "FAKE_DIR": str(fake), "ATM_IDENTITY": "lead1", "ATM_TEAM": "t1"}
        proc = subprocess.run([sys.executable, str(self.ws.scripts / "bead-dispatch"), "--phase", phase, "--once", *args],
                              cwd=self.ws.root, env=env, text=True, capture_output=True)
        assert proc.returncode == expect, (proc.returncode, proc.stdout, proc.stderr)
        return [json.loads(line) for line in proc.stdout.splitlines()]

    @staticmethod
    def lines(fake: Path, name: str) -> list:
        path = fake / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_sanity_then_qa_are_dispatched_from_bd_ready(self):
        ws = self.ws
        sprint, fake = self.phase("sq")
        dev, sanity, qa = group(sprint)
        assert self.dispatch("sq", fake) == []                       # dev open: nothing ready for the dispatcher
        assert not (fake / "gh.log").exists()                        # no GitHub read without a candidate

        ws.close(dev, "dev complete")
        self.write(fake, pulls={"sprint/sq-1": [pr(7, "sprint/sq-1", "integrate/phase-sq")]})
        events = self.dispatch("sq", fake)
        assert [(e["event"], e["bead"]) for e in events] == [("dispatched", sanity)]
        sent = self.lines(fake, "assigns.jsonl")
        assert sent[0]["argv"][:5] == ["task", "assign", "dev-sanity", "--task-id", sanity]
        assert sent[0]["argv"][sent[0]["argv"].index("--template") + 1].endswith("templates/dev-sanity-template.xml.j2")
        assert sent[0]["vars"] == {
            "task_id": sanity, "checked_bead": dev, "sprint": "sq-1", "branch": "sprint/sq-1", "commit": "a" * 40,
            "base": "integrate/phase-sq", "pr_number": 7, "pr_url": "https://x/pull/7",
            "worktree_path": str(ws.root / "wt/sprint/sq-1"), "primary_checkout": str(ws.root),
            "lint_command": "make lint", "layer_prs": []}
        assert ws.show(sanity)["assignee"] == "dev-sanity"
        assert (fake / "gh.log").read_text().splitlines() == ["repos/{owner}/{repo}/pulls?head={owner}:sprint/sq-1&state=open"]

        # The open task suppresses a second send; QA waits on the sanity PASS.
        self.write(fake, tasks=[{"task_id": sanity, "state": "active", "assignee": "dev-sanity"}])
        assert self.dispatch("sq", fake) == [] and len(self.lines(fake, "assigns.jsonl")) == 1

        ws.close(sanity, "PASS at aaaaaaa")
        self.write(fake, tasks=[], stacks=[stack(3, 7)], runs=[{"name": "tests", "conclusion": "success"}, {"name": "lint", "conclusion": None}])
        events = self.dispatch("sq", fake)
        assert [(e["event"], e["bead"]) for e in events] == [("dispatched", qa)]
        sent = self.lines(fake, "assigns.jsonl")[-1]
        assert sent["argv"][:5] == ["task", "assign", "quality-mgr", "--task-id", qa]
        assert sent["vars"] == {
            "task_id": qa, "checked_bead": dev, "phase": "sq", "phase_feature": "t-phase-sq", "sprint": "sq-1",
            "stack": "phase-sq", "layer": 1, "round": 1, "branch": "sprint/sq-1", "commit": "a" * 40,
            "base": "integrate/phase-sq", "pr_number": 7, "pr_url": "https://x/pull/7",
            "worktree_path": str(ws.root / "wt/sprint/sq-1"), "policy_path": "policy.md",
            "reviewers_round1": ["req-qa", "arch-qa"], "sprint_bead": sprint, "carry_forward": "", "layer_prs": []}

    def test_qa_holds_off_the_stack_top_and_on_red_and_announces_once(self):
        ws = self.ws
        sprint, fake = self.phase("qh")
        dev, sanity, qa = group(sprint)
        ws.close(dev, "dev complete")
        ws.close(sanity, "PASS at aaaaaaa")
        self.write(fake, pulls={"sprint/qh-1": [pr(7, "sprint/qh-1", "integrate/phase-qh")]}, stacks=[stack(3, 7, 8)])
        events = self.dispatch("qh", fake)
        assert [(e["event"], e["bead"]) for e in events] == [("hold", qa)] and "not the top of stack #3" in events[0]["reason"]
        sends = self.lines(fake, "sends.jsonl")
        assert len(sends) == 1 and sends[0]["to"] == "omega" and qa in sends[0]["body"]
        calls = len((fake / "gh.log").read_text().splitlines())
        assert self.dispatch("qh", fake) == []                       # held: no GitHub read until --recheck passes
        assert len((fake / "gh.log").read_text().splitlines()) == calls

        self.write(fake, stacks=[stack(3, 7)], runs=[{"name": "tests", "conclusion": "failure"}])
        events = self.dispatch("qh", fake, "--recheck", "0")
        assert events[0]["event"] == "hold" and "is red (tests)" in events[0]["reason"]
        events = self.dispatch("qh", fake, "--recheck", "0")
        assert events[0]["event"] == "hold" and len(self.lines(fake, "sends.jsonl")) == 2   # same reason: announced once
        assert not self.lines(fake, "assigns.jsonl") and ws.show(qa).get("assignee") is None

        self.write(fake, runs=[{"name": "tests", "conclusion": "success"}])
        assert [e["event"] for e in self.dispatch("qh", fake, "--recheck", "0")] == ["dispatched"]

    def test_sanity_holds_without_a_pr_or_worktree_and_dry_run_writes_nothing(self):
        ws = self.ws
        sprint, fake = self.phase("sh")
        dev, sanity, _ = group(sprint)
        ws.close(dev, "dev complete")
        events = self.dispatch("sh", fake)
        assert events[0]["event"] == "hold" and "no open PR on sprint/sh-1" in events[0]["reason"]
        shutil.rmtree(ws.root / "wt/sprint/sh-1")
        self.write(fake, pulls={"sprint/sh-1": [pr(9, "sprint/sh-1", "integrate/phase-sh")]})
        events = self.dispatch("sh", fake, "--recheck", "0")
        assert events[0]["event"] == "hold" and "no worktree at" in events[0]["reason"]
        (ws.root / "wt/sprint/sh-1").mkdir(parents=True)
        events = self.dispatch("sh", fake, "--recheck", "0", "--dry-run")
        assert [e["event"] for e in events] == ["would_dispatch"]
        assert not self.lines(fake, "assigns.jsonl") and ws.show(sanity).get("assignee") is None

    def test_fix_group_qa_carries_the_fix_and_the_next_round(self):
        ws = self.ws
        sprint, fake = self.phase("fx")
        dev, sanity, qa = group(sprint)
        for bead in (dev, sanity):
            ws.close(bead, "done")
        ws.groups("--findings", findings_file(ws, sprint, qa, 1, {"ref": "qa1-f1"}))
        ws.close(qa, "FAIL: 1 blocking")
        fix, fix_sanity, fix_qa = fix_group(sprint, "qa1-f1")
        ws.close(fix, "fixed")
        ws.close(fix_sanity, "PASS at bbbbbbb")
        (ws.root / "wt/fix/fx-1-qa1-f1").mkdir(parents=True)
        self.write(fake, pulls={"fix/fx-1-qa1-f1": [pr(12, "fix/fx-1-qa1-f1", "sprint/fx-1", "b" * 40)]}, stacks=[stack(4, 7, 12)])
        assert [e["event"] for e in self.dispatch("fx", fake)] == ["dispatched"]
        values = self.lines(fake, "assigns.jsonl")[-1]["vars"]
        assert {k: values[k] for k in ("task_id", "checked_bead", "carry_forward", "round", "layer", "branch", "base")} == {
            "task_id": fix_qa, "checked_bead": fix, "carry_forward": fix, "round": 2, "layer": 2,
            "branch": "fix/fx-1-qa1-f1", "base": "sprint/fx-1"}

    def test_anchor_and_quick_fix_beads_are_never_dispatched_and_a_second_process_exits(self):
        ws = self.ws
        sprint, fake = self.phase("an")
        dev, sanity, qa = group(sprint)
        ws.bd("update", sanity, "--add-label", "anchor")
        ws.close(dev, "dev complete")
        assert self.dispatch("an", fake) == []                       # the ready sanity bead is an anchor
        ws.close(sanity, "PASS at aaaaaaa")
        ws.bd("update", qa, "--set-metadata", "quick_fix=true")
        assert qa in ws.ready() and self.dispatch("an", fake) == []  # a quick-fix QA stays with the lead
        assert not (fake / "gh.log").exists()
        lock = ws.root / ".atm-bd/bead-dispatch/t-phase-an.lock"
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time; f=open(sys.argv[1],'w'); fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(30)", str(lock)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        assert holder.stdout.readline().strip() == "locked"
        self.dispatch("an", fake, expect=4)


if __name__ == "__main__":
    unittest.main()
