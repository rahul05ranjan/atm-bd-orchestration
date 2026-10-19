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


def read_vars(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CloseError("VARS.INVALID", f"Cannot read vars file {path}: {error}",
                         f"Provide a readable UTF-8 JSON file at --vars {path}.") from error
    try:
        values = json.loads(text)
    except json.JSONDecodeError as error:
        raise CloseError("VARS.INVALID", f"{path}:{error.lineno}:{error.colno}: {error.msg}",
                         f"Correct JSON at line {error.lineno}, column {error.colno} in {path}.") from error
    if not isinstance(values, dict):
        raise CloseError("VARS.INVALID", f"{path}: expected a JSON object, got {type(values).__name__}",
                         f"Replace the top-level value in {path} with the lead's shared vars object.")
    return values


def read_toml_setting(path, fields):
    name = ".".join(fields)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise CloseError("CONFIG.INVALID", f"Cannot read {name} from {path}: {error}",
                         f"Create or repair UTF-8 TOML file {path} and set {name} to a non-empty string.") from error
    for field in fields:
        if not isinstance(data, dict) or field not in data:
            raise CloseError("CONFIG.INVALID", f"{path}: missing {name}",
                             f"Set {name} to a non-empty string in {path}.")
        data = data[field]
    if not isinstance(data, str) or not data.strip():
        raise CloseError("CONFIG.INVALID", f"{path}: {name} must be a non-empty string",
                         f"Set {name} to a non-empty string in {path}.")
    return data


def read_config(root, role, verdict):
    path = root / repo_config.RELATIVE_PATH
    try:
        config = repo_config.load(root)
    except (repo_config.ConfigError, OSError, UnicodeError) as error:
        if isinstance(error, repo_config.ConfigNotFound):
            fix = f"Run the atm-bd-orchestration installer for {root} to create {path}."
        elif "max_sanity_rounds" in str(error):
            fix = f"Set max_sanity_rounds to a positive integer in the consumer registry and rerun the installer to regenerate {path}."
        elif "lead belongs" in str(error):
            fix = f"Move lead from {path} into .atm-bd/phase-<x>.toml and rerun the installer to remove the repository lead."
        else:
            fix = f"Repair the readable UTF-8 YAML mapping in {path} at the location reported in the error."
        raise CloseError("CONFIG.INVALID", f"{path}: {error}",
                         fix) from error
    fields = ["bead_prefix", "dev_sanity_member"] if role in ("dev", "fix") else ["qa_member"] if role == "sanity" and verdict == "PASS" else []
    for field in fields:
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CloseError("CONFIG.INVALID", f"{path}: {field} must be a non-empty string",
                             f"Set {field} in the consumer registry and rerun the installer to regenerate {path}.")
    return config


def sanity_round(metadata, bead):
    count = metadata.get("sanity_round", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise CloseError("BEAD.INVALID", f"{bead}: metadata.sanity_round must be a non-negative integer, got {count!r}",
                         f"Correct metadata.sanity_round on bead {bead} to the recorded non-negative FAIL count with bd update {bead} --metadata, preserving other metadata.")
    return count


def validate_command_json(argv, data):
    producer = " ".join(argv)

    def require(condition, field, expected):
        if not condition:
            raise CloseError("COMMAND.INVALID", f"{producer}: {field} must be {expected}",
                             f"Repair {field} in the JSON returned by `{producer}` to be {expected}, then retry.")

    if argv[:2] in (["bd", "show"], ["bd", "list"]):
        require(isinstance(data, list), "$", "an array of bead objects")
        for index, row in enumerate(data):
            field = f"$[{index}]"
            require(isinstance(row, dict), field, "a bead object")
            metadata = row.get("metadata")
            require(metadata is None or isinstance(metadata, dict), field + ".metadata", "an object or null")
            if isinstance(metadata, dict) and "layers" in metadata:
                layers = metadata["layers"]
                require(isinstance(layers, list), field + ".metadata.layers", "an array")
                if layers:
                    layer_index, layer = len(layers) - 1, layers[-1]
                    layer_field = f"{field}.metadata.layers[{layer_index}]"
                    require(isinstance(layer, dict), layer_field, "an object")
                    for name in ("branch", "head"):
                        require(isinstance(layer.get(name), str) and bool(layer[name].strip()), layer_field + "." + name, "a non-empty string")
    elif argv[:3] == ["atm", "task", "events"]:
        require(isinstance(data, dict), "$", "an object")
        events = data.get("events")
        require(isinstance(events, list), "$.events", "an array")
        for index, event in enumerate(events):
            field = f"$.events[{index}]"
            require(isinstance(event, dict), field, "an event object")
            if event.get("event") in ("assigned", "updated", "reassigned"):
                require(isinstance(event.get("at"), str) and bool(event["at"]), field + ".at", "a timestamp string")
                require(bool(event.get("message_id")) or isinstance(event.get("seq"), int), field + ".message_id/seq", "an assignment event identifier")
    elif argv[:2] == ["atm", "search"]:
        require(isinstance(data, dict), "$", "an object")
        require(isinstance(data.get("hits"), list), "$.hits", "an array")
    elif argv[:2] == ["gh", "api"]:
        require(isinstance(data, list), "$", "an array of PR objects")
        for index, pr in enumerate(data):
            field = f"$[{index}]"
            require(isinstance(pr, dict), field, "a PR object")
            require("number" in pr, field + ".number", "a PR number")
            require(isinstance(pr.get("head"), dict), field + ".head", "an object")
            require(isinstance(pr["head"].get("sha"), str), field + ".head.sha", "a string")
    return data


def validate(values, role):
    if role == "dev" and values.get("outcome") == "not_reproducible":
        raise CloseError("VARS.INVALID", "not_reproducible is a fix outcome", "Use the fix role for an unreproducible finding.")
    contract = header(TEMPLATES / "task-close-vars.j2")
    role_required = contract.get("required_variables_by_role")
    if not isinstance(role_required, dict) or role not in role_required:
        raise CloseError("VARS.INVALID", "task-close-vars must declare requirements by role", "Repair the role-specific required_variables mapping.")
    required = set(role_required[role])
    declared = set(contract["required_variables"])
    missing = required - values.keys()
    if role == "fix" and values.get("outcome") == "not_reproducible":
        missing -= {"branch", "commit", "pr_number"}
    unknown = values.keys() - declared - contract.get("defaults", {}).keys()
    if missing:
        raise CloseError("VARS.MISSING", ", ".join(sorted(missing)), "Supply every shared template variable in the lead's vars file.")
    if unknown:
        raise CloseError("VARS.UNKNOWN", ", ".join(sorted(unknown)), "Remove variables no shared template declares.")
    names = ["phase", "sprint", "primary_checkout", "worktree_path", "checked_bead", "dev_task_id", "sanity_task_id", "qa_task_id"]
    if values["outcome"] != "not_reproducible":
        names += ["branch", "commit", "pr_number"]
    elif not re.search(r"\S+:\d+", str(values["evidence"])):
        raise CloseError("VARS.INVALID", "not_reproducible requires evidence file:line", "Supply the authoritative absence location.")
    for name in names:
        if not str(values[name] or "").strip():
            raise CloseError("VARS.EMPTY", name, f"Populate {name} in the shared vars file.")
    for name in ("primary_checkout", "worktree_path", "phase"):
        if not isinstance(values[name], str):
            raise CloseError("VARS.INVALID", f"{name} must be a string, got {type(values[name]).__name__}",
                             f"Set {name} to a non-empty string in the lead's shared vars file.")
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
        config = read_config(self.root, role, values["verdict"])
        self.config = config
        self.lead = read_toml_setting(self.root / ".atm-bd" / f"phase-{values['phase']}.toml", ("lead",))
        team = read_toml_setting(self.root / ".atm.toml", ("atm", "default_team"))
        self.env = dict(os.environ)
        self.lead_env = {**self.env, "ATM_IDENTITY": self.lead, "ATM_TEAM": team}
        self.github = "ok"
        self.closed, self.assigned = [], []
        self.task = values[{"dev": "dev_task_id", "fix": "dev_task_id", "sanity": "sanity_task_id", "qa": "qa_task_id"}[role]]
        self.events = self.json(["atm", "task", "events", self.task, "--all", "--json"]).get("events", [])
        assignments = [event for event in self.events if event.get("event") in ("assigned", "updated", "reassigned")]
        if not assignments:
            raise CloseError("REPLAY.CONFLICT", f"{self.task} has no assignment event", "Assign the tracked task before closing it.")
        self.assignment = assignments[-1]
        self.assignment_id = self.assignment.get("message_id") or self.assignment["seq"]
        self.key = [self.task, values["verdict"], values.get("commit", "")]

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
        self.last_json_argv = list(argv)
        try:
            data = json.loads(self.run(argv, **kwargs).stdout)
        except ValueError as error:
            producer = " ".join(argv)
            raise CloseError("COMMAND.INVALID", f"{producer} returned invalid JSON: {error}",
                             f"Repair the JSON returned by `{producer}` at the reported syntax location and retry.") from error
        return validate_command_json(argv, data)

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
        metadata = record.get("metadata") or {}
        recorded = metadata.get("task_close_key")
        recorded_assignment = metadata.get("task_close_assignment", self.assignment_id)
        if isinstance(recorded, list) and len(recorded) == 4:
            recorded_assignment = recorded[1]
            recorded = [recorded[0], *recorded[2:]]
        same_assignment = recorded_assignment == self.assignment_id
        if recorded and recorded[0] == self.task and same_assignment and recorded != self.key:
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
                     "--var", f"round={values['round']}"]
            if "commit" in values:
                argv += ["--var", f"commit={values['commit']}"]
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
        self.metadata(values["checked_bead"], {"commit": assignment_values["commit"], "branch": assignment_values["branch"], "pr_number": values.get("pr_number", ""),
                                             "pr_url": values["pr_url"], "task_close_key": self.key, "task_close_assignment": self.assignment_id})
        assigned = self.delivered("dev-sanity-task", recipient=self.config["dev_sanity_member"],
                                  sender=self.lead, values=assignment_values)
        sanity_events = self.json(["atm", "task", "events", sanity, "--all", "--json"]).get("events", [])
        if sanity_events and sanity_events[-1].get("to_state") in ("completed", "refused", "cancelled"):
            if self.bead(sanity).get("status") == "open":
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
            self.update(bead, status="open", assignee="", metadata={**metadata, "task_close_key": self.key, "task_close_assignment": self.assignment_id})
            self.close_task(bead, "dev-sanity-complete.md.j2", outcome="refused")
            return
        self.render("dev-sanity-complete.md.j2")
        if verdict == "PASS":
            checked = self.bead(values["checked_bead"])
            is_fix = (checked.get("metadata") or {}).get("role") == "fix" or "stage:fix" in checked.get("labels", [])
            if not (is_fix and values["outcome"] == "not_reproducible") and (not isinstance(values["stacked"], dict) or values["stacked"].get("result") != "stacked"):
                raise CloseError("SANITY.NOT_STACKED", "PASS has no stacked result", "Link the checked layer through sanity-stack before closing PASS.")
            self.render("qa-template.xml.j2")
            if is_fix and values.get("carry_forward", "") != values["checked_bead"]:
                raise CloseError("VARS.INVALID", "fix QA requires carry_forward", "Supply the checked fix bead as carry_forward.")
            if values.get("carry_forward") and values["carry_forward"] != values["checked_bead"]:
                raise CloseError("VARS.INVALID", "carry_forward differs from fix bead", "Supply the checked fix bead as carry_forward.")
            self.metadata(bead, {"task_close_key": self.key, "task_close_assignment": self.assignment_id})
            self.close_bead(bead, f"PASS at {values['commit']}")
            self.close_task(values["dev_task_id"], lead=True)
            self.close_task(bead, "dev-sanity-complete.md.j2")
            self.assign(self.config["qa_member"], values["qa_bead"], "qa-template.xml.j2")
            return
        self.render("sanity-fail.xml.j2")
        record = self.bead(bead)
        metadata = record.get("metadata") or {}
        count_key = [self.task, self.assignment_id, values["verdict"], values.get("commit", "")]
        count = sanity_round(metadata, bead)
        if metadata.get("sanity_round_key") != count_key:
            count += 1
            self.metadata(bead, {"sanity_round": count, "sanity_round_key": count_key, "task_close_key": self.key, "task_close_assignment": self.assignment_id})
        if count > self.config["max_sanity_rounds"]:
            message = f"SANITY.ROUND_CAP {bead} {values['commit']} assignment={self.assignment_id} undone=" + ",".join(str(item["number"]) for item in values["undone"])
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
        self.metadata(values["qa_bead"], {"task_close_key": self.key, "task_close_assignment": self.assignment_id})
        self.close_bead(values["qa_bead"], f"{values['verdict']} at {values['commit']}")
        self.close_task(values["qa_bead"], "qa-complete.md.j2")
        message = f"QA {values['verdict']} {values['qa_bead']} commit={values['commit']} round={values['round']}"
        if not self.delivered(None, recipient=self.lead, text=message):
            self.run(["atm", "send", self.lead, message])


def main(argv=None, runner=subprocess.run, clock=time.monotonic):
    parser = Parser(description=__doc__)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--vars", required=True, type=Path)
    parser.add_argument("--skip-checks", action="store_true", help="skips the PR and stack checks. Do NOT use without permission from the phase lead.")
    hook = None
    try:
        args = parser.parse_args(argv)
        values = read_vars(args.vars)
        validate(values, args.role)
        hook = Hook(args.role, values, args.vars, runner, clock)
        started = clock()
        escalated = hook.dev(args.skip_checks) if args.role in ("dev", "fix") else hook.sanity() if args.role == "sanity" else hook.qa()
        result = {"role": args.role, "bead": hook.task, "commit": values.get("commit", ""), "pr_number": values.get("pr_number", ""),
                  "github": hook.github, "assigned": hook.assigned, "closed": hook.closed, "elapsed_s": max(0, clock() - started)}
        if escalated:
            result["escalated"] = escalated
        output = {"ok": result}
    except CloseError as error:
        output = {"err": {"code": error.code, "message": str(error), "fix": error.fix}}
    except KeyError as error:
        field = str(error.args[0])
        producer = " ".join(hook.last_json_argv) if hook is not None else str(args.vars)
        output = {"err": {"code": "COMMAND.INVALID", "message": f"{producer}: required field {field} is missing",
                          "fix": f"Restore field {field} in `{producer}` before retrying."}}
    except OSError as error:
        path = str(error.filename or "the path named in the error")
        output = {"err": {"code": "COMMAND.FAILED", "message": str(error),
                          "fix": f"Restore file access for {path} and rerun with the same vars file."}}
    except (ValueError, TypeError) as error:
        producer = " ".join(hook.last_json_argv) if hook is not None else str(args.vars)
        output = {"err": {"code": "COMMAND.INVALID", "message": f"{producer}: {type(error).__name__}: {error}",
                          "fix": f"Repair the invalid value reported by {type(error).__name__} in `{producer}`: {error}."}}
    print(json.dumps(output))
    print(json.dumps(output), file=sys.stderr)
    return 1 if "err" in output else 0


if __name__ == "__main__":
    raise SystemExit(main())
