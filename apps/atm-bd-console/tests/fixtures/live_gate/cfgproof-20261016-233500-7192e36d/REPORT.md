# Configured-Template Delivery Proof Report

Run ID: cfgproof-20261016-233500-7192e36d
Date: 2026-10-16T23:37Z - 2026-10-16T23:40Z
Fixture: d627396c839f (Loki's isolated Hermes testbed)
Agent: Hermes (single agent, two fixed shell identity contexts)
WRITE_SAFE_ROOT: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/

## Source and Tool Pins

- Product source: b470d33296d745b095187288073492624c72f934 (tag v0.16.0, https://github.com/randlee/atm-bd-orchestration.git)
- atm-bd-console: 0.1.0 installed from frozen source into scratch venv
- Console __file__: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/scratch/venv/lib/python3.13/site-packages/atm_bd_console/__init__.py
- ATM CLI: 1.6.2
- ATM daemon: 1.6.2
- bd: 1.3.1 (c1c4b642a)
- Dolt: 2.3.1 (embedded mode)
- git: 2.47.3
- Python: 3.13.5
- ATM doctor: healthy (0 warnings, 0 errors)
- Hermes pin: v0.22.2-atm (fixture-provided)

### Command locations

- atm-bd-console: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/scratch/venv/bin/atm-bd-console
- atm: /usr/local/bin/atm
- bd: /usr/local/bin/bd
- git: /usr/bin/git

## ATM Team and Roster

Team: cfgproof-20261016-233500-7192e36d
Members:
- cfg-lead: agent_type=lead, home_dir=CFG_REPO
- cfg-receiver: agent_type=stub, backend=herdr, home_dir=CFG_REPO

Pre-launch task list: empty ([])

## Identity Contexts

Two fixed shell contexts operated by the same single agent (Hermes), each with one ATM_IDENTITY for its entire lifetime:
- Launcher context: ATM_IDENTITY=cfg-lead, ATM_TEAM=cfgproof-20261016-233500-7192e36d
- Receiver context: ATM_IDENTITY=cfg-receiver, ATM_TEAM=cfgproof-20261016-233500-7192e36d

This is deliberately a same-identity launcher proof. It does not test or claim cross-identity mutation. The receiver context was operated by the same agent, not an independent LLM process.

## Phase Configuration

Phase TOML (.atm-bd/phase-cfgproof.toml):
```
plan = "docs/plans/phase-cfgproof.jsonl"
root = "proof-phase-cfgproof"
integration_branch = "main"
lead = "cfg-lead"

[templates]
dev = ".claude/skills/atm-bd-orchestration/templates/dev-template.xml.j2"
dev_fix = ".claude/skills/atm-bd-orchestration/templates/dev-fix.xml.j2"
fix = ".claude/skills/atm-bd-orchestration/templates/fix-assignment.xml.j2"
sanity = ".claude/skills/atm-bd-orchestration/templates/cfgproof/sanity.xml.j2"
qa = ".claude/skills/atm-bd-orchestration/templates/qa-template.xml.j2"
plan_review = ".claude/skills/atm-bd-orchestration/templates/plan-review-template.xml.j2"
review = ".claude/skills/atm-bd-orchestration/templates/review-template.xml.j2"
```

All seven template paths verified as existing regular files.

Project YAML (.claude/project/atm-bd-orchestration.yaml):
```
bead_prefix: "proof"
dev_sanity_member: "cfg-receiver"
qa_member: "cfg-receiver"
worktree_base: "/opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d"
lint_command: "git diff --check"
policy_path: ".claude/project/quality-policy.md"
reviewers_round1: []
plans_dir: "docs/plans"
```

## Custom Template

Path: .claude/skills/atm-bd-orchestration/templates/cfgproof/sanity.xml.j2
Name: cfgproof-sanity-cfgproof-20261016-233500-7192e36d
Marker: CFGPROOF_CONFIGURED_cfgproof-20261016-233500-7192e36d
File size: 1168 bytes, UTF-8, LF-only

Basename-fallback control at repo root (sanity.xml.j2):
Name: cfgproof-basename-fallback-cfgproof-20261016-233500-7192e36d
Marker: CFGPROOF_BASENAME_FALLBACK_cfgproof-20261016-233500-7192e36d

Default production template (dev-sanity-template.xml.j2): left unchanged.

Only the sanity purpose was exercised live; the other six configured template paths were verified present but not dispatched.

## Template Identity Verification

### Canonical Content SHA (sc-sha TextFileBytes normalization)

Contract: sc-sha 1.7.0 src/file.rs:130 — strict UTF-8 decoding, universal newline normalization (CRLF/bare CR → LF), preserve other Unicode including BOM, SHA256 over resulting UTF-8 bytes.

Actual template path: .claude/skills/atm-bd-orchestration/templates/cfgproof/sanity.xml.j2
Raw byte length: 1168
Has CRLF: False
Has bare CR: False
LF-only: True (no normalization needed for this file)
Canonical content SHA256: efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb
Raw file SHA256: efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb
Raw == Canonical: True

### Catalog Observation

atm templates list --type cfgproof-sanity:
- template_sha: efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb
- first_seen_by: cfg-lead
- first_seen_at: 2026-10-16T23:37:59.288432245+00:00

### Comparison

Canonical content SHA == Catalog template_sha: TRUE
Both: efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb

### Correlated Search

atm search --template-sha efa6cd54... --var task_id=proof-cfgproof-1.group-sanity --team CFG_TEAM --agent cfg-receiver:
- message_id: 01M53H8Q7R59YY5ZVX1XNS6YVG
- from_agent: cfg-lead
- to_agent: cfg-receiver
- template_type: cfgproof-sanity
- workflow.snapshot.scope_id: cfgproof-1
- workflow.snapshot.state: dev-sanity-start

The delivered message template_sha matches the canonical content SHA of the actual phase-configured PATH.

## Beads Graph

Five beads created:
- proof-phase-cfgproof: open epic, label phase-cfgproof
- proof-cfgproof-1: open epic, labels phase-cfgproof + stage:sprint, parent=proof-phase-cfgproof
- proof-cfgproof-1.group-dev: closed/completed task, labels phase-cfgproof + stage:dev, parent=proof-cfgproof-1
- proof-cfgproof-1.group-sanity: closed/completed task, labels phase-cfgproof + stage:dev-sanity, parent=proof-cfgproof-1, blocked-by=dev
- proof-cfgproof-1.group-qa: open task, labels phase-cfgproof + stage:qa, parent=proof-cfgproof-1, blocked-by=sanity

Pre-launch readiness: sanity=ready (dev closed, blocker satisfied), qa=not ready (sanity open).

## Git Repository

CFG_REPO: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/scratch/repo
Branch: sprint/cfgproof-1
Branch SHA: f97cf1b1143916448144a92298e18e014e607c44
Origin: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/scratch/origin.git
Origin refs: main and sprint/cfgproof-1 both at f97cf1b1143916448144a92298e18e014e607c44
Beads storage: embedded Dolt, prefix=proof

## Console Execution

Command: atm-bd-console --phase cfgproof --headless --github off
CWD: /opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/scratch/repo
Context: ATM_IDENTITY=cfg-lead ATM_TEAM=cfgproof-20261016-233500-7192e36d
No --as option. No --no-fetch. No --dry-run. No warm-up tick.
Start: 2026-10-16T23:37:58Z
End: 2026-10-16T23:37:59Z
Exit code: 0

Console stdout (complete):
```
{"at":"2026-10-16T23:37:58Z","bead":"proof-cfgproof-1.group-sanity","kind":"assign","outcome":"sent","text":"dispatch proof-cfgproof-1.group-sanity","to":"cfg-receiver"}
```

One assignment dispatched. No QA assignment (held with reason "not ready").

### Snapshot observations

snapshot.json confirms:
- lead: "cfg-lead" (matches phase TOML lead)
- sent[0].spec.template: ".claude/skills/atm-bd-orchestration/templates/cfgproof/sanity.xml.j2" (the configured path)
- sent[0].to: "cfg-receiver"
- sent[0].spec.vars.commit: "f97cf1b1143916448144a92298e18e014e607c44"
- sent[0].spec.vars.task_id: "proof-cfgproof-1.group-sanity"
- sent[0].outcome: "sent"
- sections.Held: proof-cfgproof-1.group-qa with reason "not ready"

The console read the phase lead (cfg-lead), selected the configured sanity template path, and dispatched exactly one assignment.

## Delivery Observations

### Task state (ATM daemon)

Task list after console:
- task_id: proof-cfgproof-1.group-sanity
- assignee: cfg-receiver
- assigner: cfg-lead
- state: assigned
- assignment_message_id: 01M53H8Q7R59YY5ZVX1XNS6YVG
- description contains: CFGPROOF_CONFIGURED_cfgproof-20261016-233500-7192e36d

The configured marker is present. Neither the basename-fallback marker (CFGPROOF_BASENAME_FALLBACK_) nor the default production assignment body appears.

### Receiver mailbox read (cfg-receiver context)

message_id: 01M53H8Q7R59YY5ZVX1XNS6YVG
from: cfg-lead
body: <atm-task id="proof-cfgproof-1.group-sanity" sprint="cfgproof-1" mode="dev-sanity"> with CFGPROOF_CONFIGURED_cfgproof-20261016-233500-7192e36d marker
commit rendered: f97cf1b1143916448144a92298e18e014e607c44

### Commit correlation

Rendered commit in assignment: f97cf1b1143916448144a92298e18e014e607c44
Origin sprint/cfgproof-1 SHA: f97cf1b1143916448144a92298e18e014e607c44
proof.txt content: CFGPROOF_CONFIGURED_cfgproof-20261016-233500-7192e36d
MATCH: rendered commit == origin branch SHA == worktree HEAD

## Receiver Lifecycle

### task_ready

NOT observed. Polled for 30 seconds; only the "assigned" event was present. The cfg-receiver herdr backend has no live receiver session for this isolated team. This is a supporting evidence limit; the core delivery observation does not depend on task_ready.

### task start

Bead claimed: bd update proof-cfgproof-1.group-sanity --claim --actor cfg-receiver (success)
ATM task start: atm task start proof-cfgproof-1.group-sanity "checking the delivered marker and scratch commit"
Result: sent, message_id=01M53HC3MQZTMYP1WBK34M2C0V
Event: started, actor=cfg-receiver, from_state=assigned, to_state=active

### task close

Bead closed: bd close proof-cfgproof-1.group-sanity --resolution completed --actor cfg-receiver (success)
ATM task close: atm task close proof-cfgproof-1.group-sanity completed --template cfgproof/complete.md.j2 --vars completion-vars.json
Result: sent, message_id=01M53HCKR2NASNQ0WDJ71GM2KF
Event: completed, actor=cfg-receiver, from_state=active, to_state=complete, close_outcome=completed

### Handoffs observed (to cfg-lead)

- task_started handoff at 2026-10-16T23:39:50.295Z, message_key=01M53HC3MQZTMYP1WBK34M2C0V
- task_complete handoff at 2026-10-16T23:40:06.786Z, message_key=01M53HCKR2NASNQ0WDJ71GM2KF

### No extra assignments

Final task list: empty (sanity completed, no QA assignment produced)
proof-cfgproof-1.group-qa: status=open (held, no dispatch occurred)
The console was not run again after closing sanity.

## Evidence Not Captured

1. task_ready event: not emitted (no live herdr session for cfg-receiver on this team)
2. Child atm task assign argv: the console captures and discards successful child stdout inside atm_source.assign; the raw child argv is not exposed by approved public observations
3. template_name in catalog: returned as null (the daemon stores template_type but not template_name in the catalog entry)

## Conclusion

The frozen console (v0.16.0, b470d33) read the phase-cfgproof.toml lead (cfg-lead) and the configured sanity template path (.claude/skills/atm-bd-orchestration/templates/cfgproof/sanity.xml.j2). From exactly one tick (--headless), it produced one real native ATM assignment to cfg-receiver.

The canonical content SHA of the actual phase-configured sanity PATH (efa6cd540565952a510244105e199cfb6ff8441591d4192c129653416c8385eb, computed via the documented sc-sha TextFileBytes normalization) matches both the admitted catalog template_sha and the correlated delivered-message/search template_sha. The delivered body contains the configured custom marker (CFGPROOF_CONFIGURED_), not the basename-fallback marker or the default production template body. The rendered commit matches the actual origin branch SHA. The assigner field (cfg-lead) matches the fixed launcher ATM_IDENTITY and the phase TOML lead. snapshot.lead confirms cfg-lead.

Real task start and close were exercised through the preestablished cfg-receiver context. task_ready was not observed (herdr backend limitation for this isolated team). The completion was delivered to cfg-lead via the paired custom completion template. No QA assignment was produced (held, not ready). No second console tick was run.

This constitutes real CLI/daemon delivery to an isolated protocol receiver operated by one agent in Loki's isolated Hermes testbed container d627396c839f.

## Evidence Directory

/opt/data/results/rel-162.2-scopeC-v5/cfgproof-20261016-233500-7192e36d/evidence/
