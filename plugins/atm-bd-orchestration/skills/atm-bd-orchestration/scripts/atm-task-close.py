#!/usr/bin/env python3
"""Close orchestration beads and hand off work without closing a dev task early."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "atm-beads/scripts"))
import repo_config
import sc_render

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
ROLES = ("dev", "fix", "sanity", "qa")


class CloseError(RuntimeError):
    def __init__(self, code, message, fix):
        super().__init__(message)
        self.code, self.fix = code, fix


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CloseError("ARGS.INVALID", message, "Supply --role dev|fix|sanity|qa and --vars <shared-vars.json>.")


def header(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def validate(values, role):
    if role == "dev" and values.get("outcome") == "not_reproducible":
        raise CloseError("VARS.INVALID", "not_reproducible is a fix outcome", "Use the fix role for an unreproducible finding.")
    contract = header(TEMPLATES / "task-close-vars.j2")
    required = set(contract["required_variables"])
    missing = required - values.keys()
    if values.get("outcome") == "not_reproducible":
        missing -= {"branch", "commit", "pr_number"}
    unknown = values.keys() - required - contract.get("defaults", {}).keys()
    if missing:
        raise CloseError("VARS.MISSING", ", ".join(sorted(missing)), "Supply every shared template variable in the lead's vars file.")
    if unknown:
        raise CloseError("VARS.UNKNOWN", ", ".join(sorted(unknown)), "Remove variables no shared template declares.")
    try:
        sc_render.render(TEMPLATES / "task-close-vars.j2", values)
    except sc_render.RenderError as error:
        raise CloseError("VARS.INVALID", str(error), "Correct the shared vars file before retrying.") from error
    names = ["phase", "sprint", "primary_checkout", "worktree_path", "checked_bead", "dev_task_id", "sanity_task_id", "qa_task_id"]
    if values["outcome"] != "not_reproducible":
        names += ["branch", "commit", "pr_number"]
    elif not re.search(r"\S+:\d+", str(values["evidence"])):
        raise CloseError("VARS.INVALID", "not_reproducible requires evidence file:line", "Supply the authoritative absence location.")
    for name in names:
        if not str(values[name] or "").strip():
            raise CloseError("VARS.EMPTY", name, f"Populate {name} in the shared vars file.")
    if role in ("sanity", "qa") and values["verdict"] not in ("PASS", "FAIL", "CANNOT_RUN"):
        raise CloseError("VARS.INVALID", "verdict must be PASS, FAIL or CANNOT_RUN", "Supply the selected review verdict.")
    if role == "qa" and values["verdict"] == "CANNOT_RUN":
        raise CloseError("VARS.INVALID", "QA CANNOT_RUN is not a completed review", "Use the QA refusal workflow.")
    if not isinstance(values["undone"], list) or any(
        not isinstance(item, dict) or isinstance(item.get("number"), bool)
        or not isinstance(item.get("number"), int) or item["number"] < 1
        or not isinstance(item.get("reason"), str) or not item["reason"].strip()
        for item in values["undone"]
    ):
        raise CloseError("VARS.INVALID", "undone must contain numbered deliverables and verbatim reasons", "Supply the selected undone deliverables.")
    if role == "sanity" and values["verdict"] == "FAIL" and not values["undone"]:
        raise CloseError("VARS.INVALID", "FAIL has no undone deliverables", "Supply the selected undone deliverables.")
    if values["dev_task_id"] != values["checked_bead"] or values["sanity_task_id"] != values["sanity_bead"] or values["qa_task_id"] != values["qa_bead"]:
        raise CloseError("VARS.INVALID", "task ids and bead ids differ", "Use bead ids as the corresponding task ids.")


class Hook:
    def __init__(self, role, values, path, runner, clock):
        self.role, self.values, self.path = role, values, Path(path).resolve()
        self.runner, self.clock = runner, clock
        self.root = Path(values["primary_checkout"]).resolve()
        self.worktree = Path(values["worktree_path"]).resolve()
        self.identity = os.environ.get("ATM_IDENTITY", "")
        if not self.identity:
            raise CloseError("VARS.INVALID", "ATM_IDENTITY is not set", "Run as the assigned ATM member.")
        config = repo_config.load(self.root)
        self.config = config
        phase = tomllib.loads((self.root / ".atm-bd" / f"phase-{values['phase']}.toml").read_text())
        team = tomllib.loads((self.root / ".atm.toml").read_text())["atm"]["default_team"]
        self.lead = phase["lead"]
        self.env = dict(os.environ)
        self.env["ATM_TEAM"] = team
        self.lead_env = {**self.env, "ATM_IDENTITY": self.lead}
        self.github = "ok"
        self.closed, self.assigned = [], []
        self.task = values[{"dev": "dev_task_id", "fix": "dev_task_id", "sanity": "sanity_task_id", "qa": "qa_task_id"}[role]]
        self.events = self.json(["atm", "task", "events", self.task, "--all", "--json"]).get("events", [])
        assignments = [event for event in self.events if event.get("event") in ("assigned", "updated", "reassigned")]
        if not assignments:
            raise CloseError("REPLAY.CONFLICT", f"{self.task} has no assignment event", "Assign the tracked task before closing it.")
        self.assignment = assignments[-1]
        self.key = [self.task, self.assignment.get("message_id") or self.assignment["seq"], values["verdict"], values["commit"]]

    def run(self, argv, *, lead=False, git=False, check=True):
        try:
            result = self.runner(list(argv), cwd=self.worktree if git else self.root,
                                 env=self.lead_env if lead else self.env, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise CloseError("COMMAND.FAILED", str(error), f"Repair {argv[0]} and rerun with the same vars file.") from error
        if check and result.returncode:
            raise CloseError("COMMAND.FAILED", result.stderr.strip() or result.stdout.strip(),
                             f"Repair {' '.join(argv[:3])} and rerun with the same vars file.")
        return result

    def json(self, argv, **kwargs):
        try:
            return json.loads(self.run(argv, **kwargs).stdout)
        except ValueError as error:
            raise CloseError("COMMAND.INVALID", f"{argv[0]} returned invalid JSON", "Repair the command's JSON output and retry.") from error

    def bead(self, bead):
        rows = self.json(["bd", "show", bead, "--json"])
        if not isinstance(rows, list) or len(rows) != 1:
            raise CloseError("BEAD.INVALID", bead, "Supply an existing unique bead id.")
        return rows[0]

    def update(self, bead, **fields):
        argv = ["bd", "update", bead]
        for name, value in fields.items():
            argv += ["--" + name.replace("_", "-"), json.dumps(value) if name == "metadata" else str(value)]
        self.run([*argv, "--actor", self.identity])

    def metadata(self, bead, values):
        current = self.bead(bead).get("metadata") or {}
        if all(current.get(name) == value for name, value in values.items()):
            return
        self.update(bead, metadata={**current, **values})

    def check_replay(self, bead):
        record = self.bead(bead)
        recorded = (record.get("metadata") or {}).get("task_close_key")
        if recorded and recorded[:2] == self.key[:2] and recorded != self.key:
            if self.role not in ("dev", "fix") or record.get("status") != "open":
                raise CloseError("REPLAY.CONFLICT", "recorded verdict or sha differs", "Use the original vars for a replay or start a new review assignment.")

    def stack_top(self):
        stack = f"{self.config['bead_prefix']}-phase-{self.values['phase']}.stack"
        layers = (self.bead(stack).get("metadata") or {}).get("layers", [])
        if layers:
            return layers[-1]["branch"], layers[-1]["head"]
        branch = self.values["pr_target"]
        head = self.run(["git", "rev-parse", f"origin/{branch}"], git=True).stdout.strip()
        return branch, head

    def checks(self):
        values = self.values
        head = self.run(["git", "rev-parse", "HEAD"], git=True).stdout.strip()
        if head != values["commit"]:
            raise CloseError("HEAD.NOT_PUSHED", "HEAD differs from commit", "Push the checked commit and supply its full sha.")
        dirty = self.run(["git", "status", "--porcelain"], git=True).stdout.strip()
        if dirty:
            raise CloseError("HEAD.DIRTY", dirty, "Commit or remove worktree changes before retrying.")
        remote = self.run(["git", "ls-remote", "origin", f"refs/heads/{values['branch']}"], git=True).stdout.split()
        if not remote or remote[0] != head:
            raise CloseError("HEAD.NOT_PUSHED", "origin head differs from HEAD", "Push this branch and retry.")
        top, _head = self.stack_top()
        self.run(["git", "fetch", "origin"], git=True)
        ancestor = self.run(["git", "merge-base", "--is-ancestor", f"origin/{top}", head], git=True, check=False)
        if ancestor.returncode == 1:
            raise CloseError("BASE.NOT_ANCESTOR", top, f"Rebase onto origin/{top}, push, then rerun the hook.")
        if ancestor.returncode:
            raise CloseError("COMMAND.FAILED", ancestor.stderr, "Repair the local git ancestry check.")
        remote_url = self.run(["git", "remote", "get-url", "origin"], git=True).stdout.strip()
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", remote_url)
        if not match:
            raise CloseError("COMMAND.INVALID", "origin does not name owner/repository", "Configure the repository origin.")
        owner, repo = match.groups()
        for _attempt in range(3):
            try:
                prs = self.json(["gh", "api", f"repos/{owner}/{repo}/pulls?head={owner}:{values['branch']}&state=open"], git=True)
            except CloseError as error:
                if error.code not in ("COMMAND.FAILED", "COMMAND.INVALID"):
                    raise
                continue
            if not any(str(pr["number"]) == str(values["pr_number"]) and pr["head"]["sha"] == head for pr in prs):
                raise CloseError("PR.NOT_FOUND", "no open PR for this branch at HEAD", "Open the PR at the pushed head and supply its number.")
            return
        self.github = "unavailable"

    def delivered(self, template_type, *, recipient=None, text=None, extra=(), values=None, sender=None):
        values = self.values if values is None else values
        argv = ["atm", "search"]
        if text:
            argv.append(text)
        else:
            argv += ["--type", template_type, "--var", f"checked_bead={values['checked_bead']}",
                     "--var", f"commit={values['commit']}", "--var", f"round={values['round']}"]
        argv += ["--from", sender or self.identity, "--since", self.assignment["at"], "--limit", "1", "--json"]
        if recipient:
            argv += ["--agent", recipient]
        argv += list(extra)
        return bool(self.json(argv).get("hits"))

    def render(self, template, values=None):
        try:
            return sc_render.render(TEMPLATES / template, self.values if values is None else values)
        except sc_render.RenderError as error:
            raise CloseError("VARS.INVALID", str(error), f"Supply vars compatible with {template}.") from error

    def send(self, recipient, template, template_type):
        if not self.delivered(template_type, recipient=recipient):
            self.run(["atm", "send", recipient, "--template", str(TEMPLATES / template), "--vars", str(self.path)])

    def assign(self, member, bead, template, *, path=None):
        template_type = header(TEMPLATES / template)["metadata"]["type"]
        if not self.delivered(template_type, recipient=member, sender=self.lead):
            self.run(["atm", "task", "assign", member, "--task-id", bead, "--template", str(TEMPLATES / template),
                      "--vars", str(path or self.path)], lead=True)
        self.assigned.append(bead)

    def close_task(self, task, template=None, *, outcome="completed", lead=False):
        events = self.json(["atm", "task", "events", task, "--all", "--json"]).get("events", [])
        latest = events[-1] if events else {}
        if latest.get("to_state") not in ("completed", "refused", "cancelled"):
            argv = ["atm", "task", "close", task, outcome]
            if template:
                argv += ["--template", str(TEMPLATES / template), "--vars", str(self.path)]
            self.run(argv, lead=lead)

    def close_bead(self, bead, reason):
        record = self.bead(bead)
        if record.get("status") != "closed":
            self.run(["bd", "close", bead, "--reason", reason, "--actor", self.identity])
        self.closed.append(bead)

    def dev(self, skip):
        values = self.values
        template = "fix-complete.md.j2" if self.role == "fix" else "dev-complete.md.j2"
        self.render(template)
        self.check_replay(values["checked_bead"])
        record = self.bead(values["checked_bead"])
        sanity = (record.get("metadata") or {}).get("sanity_bead")
        if not sanity:
            candidates = self.json(["bd", "list", "-l", "stage:dev-sanity", "--status", "open", "-n", "0", "--json"])
            matches = [row["id"] for row in candidates if (row.get("metadata") or {}).get("dev_bead") == values["checked_bead"]]
            sanity = matches[0] if len(matches) == 1 else None
        if sanity != values["sanity_bead"]:
            raise CloseError("BEAD.NO_SANITY", "no matching group sanity bead", f"Ask {self.lead} to create or correct the group sanity bead.")
        if values["outcome"] == "not_reproducible":
            previous = record.get("metadata") or {}
            if previous.get("task_close_key") == self.key and previous.get("branch") and previous.get("commit"):
                branch, commit = previous["branch"], previous["commit"]
            else:
                branch, commit = self.stack_top()
            assignment = {**values, "branch": branch, "commit": commit}
            self.render("dev-sanity-template.xml.j2", assignment)
            with tempfile.TemporaryDirectory(prefix="atm-task-close-") as directory:
                path = Path(directory) / "sanity-vars.json"
                path.write_text(json.dumps(assignment), encoding="utf-8")
                self.finish_dev(sanity, template, path=path)
            return
        self.render("dev-sanity-template.xml.j2")
        if not skip:
            self.checks()
        self.finish_dev(sanity, template)

    def finish_dev(self, sanity, template, *, path=None):
        values = self.values
        assignment_values = json.loads(Path(path).read_text()) if path else values
        self.metadata(values["checked_bead"], {"commit": assignment_values["commit"], "branch": assignment_values["branch"], "pr_number": values["pr_number"],
                                             "pr_url": values["pr_url"], "task_close_key": self.key})
        assigned = self.delivered("dev-sanity-task", recipient=self.config["dev_sanity_member"],
                                  sender=self.lead, values=assignment_values)
        sanity_events = self.json(["atm", "task", "events", sanity, "--all", "--json"]).get("events", [])
        if sanity_events and sanity_events[-1].get("to_state") in ("completed", "refused", "cancelled"):
            if self.bead(sanity).get("status") != "closed":
                assigned = False
        reason = f"not reproducible: {values['evidence']}" if values["outcome"] == "not_reproducible" else f"{'fixed' if self.role == 'fix' else 'dev complete'} at {values['commit']}"
        self.close_bead(values["checked_bead"], reason)
        if not assigned:
            self.run(["atm", "task", "assign", self.config["dev_sanity_member"], "--task-id", sanity,
                      "--template", str(TEMPLATES / "dev-sanity-template.xml.j2"), "--vars", str(path or self.path)], lead=True)
        self.assigned.append(sanity)
        self.send(self.lead, template, "fix-complete" if self.role == "fix" else "dev-complete")

    def sanity(self):
        values = self.values
        bead = values["sanity_bead"]
        verdict = values["verdict"]
        self.check_replay(bead)
        if verdict == "CANNOT_RUN":
            self.render("dev-sanity-complete.md.j2")
            metadata = self.bead(bead).get("metadata") or {}
            self.update(bead, status="open", assignee="", metadata={**metadata, "task_close_key": self.key})
            self.close_task(bead, "dev-sanity-complete.md.j2", outcome="refused")
            return
        self.render("dev-sanity-complete.md.j2")
        if verdict == "PASS":
            if values["outcome"] != "not_reproducible" and (not isinstance(values["stacked"], dict) or values["stacked"].get("result") != "stacked"):
                raise CloseError("SANITY.NOT_STACKED", "PASS has no stacked result", "Link the checked layer through sanity-stack before closing PASS.")
            self.render("qa-template.xml.j2")
            checked = self.bead(values["checked_bead"])
            is_fix = (checked.get("metadata") or {}).get("role") == "fix" or "stage:fix" in checked.get("labels", [])
            if is_fix and values["carry_forward"] != values["checked_bead"]:
                raise CloseError("VARS.INVALID", "fix QA requires carry_forward", "Supply the checked fix bead as carry_forward.")
            if values["carry_forward"] and values["carry_forward"] != values["checked_bead"]:
                raise CloseError("VARS.INVALID", "carry_forward differs from fix bead", "Supply the checked fix bead as carry_forward.")
            self.metadata(bead, {"task_close_key": self.key})
            self.close_bead(bead, f"PASS at {values['commit']}")
            self.close_task(values["dev_task_id"], lead=True)
            self.close_task(bead, "dev-sanity-complete.md.j2")
            self.assign(self.config["qa_member"], values["qa_bead"], "qa-template.xml.j2")
            return
        self.render("sanity-fail.xml.j2")
        record = self.bead(bead)
        metadata = record.get("metadata") or {}
        count_key = self.key
        count = int(metadata.get("sanity_round", 0))
        if metadata.get("sanity_round_key") != count_key:
            count += 1
            self.metadata(bead, {"sanity_round": count, "sanity_round_key": count_key, "task_close_key": self.key})
        if count > self.config["max_sanity_rounds"]:
            message = f"SANITY.ROUND_CAP {bead} {values['commit']} assignment={self.key[1]} undone=" + ",".join(str(item["number"]) for item in values["undone"])
            if not self.delivered(None, recipient=self.lead, text=message):
                self.run(["atm", "send", self.lead, message])
            self.close_task(bead, "dev-sanity-complete.md.j2")
            return "SANITY.ROUND_CAP"
        checked = self.bead(values["checked_bead"])
        self.update(values["checked_bead"], status="open", assignee=checked["assignee"])
        self.update(bead, status="open", assignee="")
        self.send(checked["assignee"], "sanity-fail.xml.j2", "sanity-fail")
        self.close_task(bead, "dev-sanity-complete.md.j2")

    def qa(self):
        values = self.values
        self.check_replay(values["qa_bead"])
        self.render("qa-complete.md.j2")
        self.metadata(values["qa_bead"], {"task_close_key": self.key})
        self.close_bead(values["qa_bead"], f"{values['verdict']} at {values['commit']}")
        self.close_task(values["qa_bead"], "qa-complete.md.j2")
        self.send(self.lead, "qa-complete.md.j2", "qa-complete")


def main(argv=None, runner=subprocess.run, clock=time.monotonic):
    parser = Parser(description=__doc__)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--vars", required=True, type=Path)
    parser.add_argument("--skip-checks", action="store_true", help="skips the PR and stack checks. Do NOT use without permission from the phase lead.")
    try:
        args = parser.parse_args(argv)
        values = json.loads(args.vars.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise CloseError("VARS.INVALID", "vars must be an object", "Supply a shared JSON vars object.")
        validate(values, args.role)
        values = {**header(TEMPLATES / "task-close-vars.j2")["defaults"], **values}
        hook = Hook(args.role, values, args.vars, runner, clock)
        started = clock()
        escalated = hook.dev(args.skip_checks) if args.role in ("dev", "fix") else hook.sanity() if args.role == "sanity" else hook.qa()
        result = {"role": args.role, "bead": hook.task, "commit": values["commit"], "pr_number": values["pr_number"],
                  "github": hook.github, "assigned": hook.assigned, "closed": hook.closed, "elapsed_s": max(0, clock() - started)}
        if escalated:
            result["escalated"] = escalated
        output = {"ok": result}
    except CloseError as error:
        output = {"err": {"code": error.code, "message": str(error), "fix": error.fix}}
    except (OSError, ValueError, KeyError, TypeError, repo_config.ConfigError) as error:
        output = {"err": {"code": "VARS.INVALID", "message": str(error), "fix": "Correct repository configuration or vars and rerun the hook."}}
    print(json.dumps(output))
    print(json.dumps(output), file=sys.stderr)
    return 1 if "err" in output else 0


if __name__ == "__main__":
    raise SystemExit(main())
