# Changelog

## [0.16.0] - 2026-10-14

### Changed
- `bead-queues` is now a thin `atm-bd-console show --phase <x>` snapshot viewer. It no longer reads beads, ATM tasks, or GitHub; it has no cron JSON/state-file mode. The phase cron is `atm-bd-console --once --headless --phase <x> --as <lead>`.
- Plugin installation requires `atm-bd-console >= 0.1.0`. The console owns its `mmdflux >= 2.6.1` status-rendering dependency and reports a typed fix when it is missing or too old.
- The `bead-queues` install remediation is rendered from the installer's single `CONSOLE_INSTALL` definition.

## [0.15.0] - 2026-10-14

### Changed
- `bead-queues` no longer lists closed beads (randlee/atm-bd-orchestration#126). It reads the phase's open beads with one `bd list -l phase-<x> --exclude-label anchor` and one `bd ready` with the same filters, in place of `bd list --all`, which re-read every closed bead of the phase on every run (510 KB for one phase). A closed predecessor is one that is not in the open list, and readiness comes from bd: a sanity check bead closes only on PASS, so a ready QA bead means its sanity passed. The only closed read is one `bd list --status closed --id ...` naming the beads of open ATM tasks that are not in the open list (`task_bead_closed`). The scope is the phase label rather than a parent-child walk, so an open bead under a closed parent is still read. `container_closable` now reports an open `stage:sprint` bead with no open children.
- `bead-queues` reads the stack from GitHub only with `--stack` (`gh api .../stacks` and `gh stack view --json`). Without it no `gh` command runs and the report says `stack: not checked (--stack reads it)`. The `pr_unlinked` and `stack_incoherent` queues are filled only with `--stack`.

### Removed
- `bead-queues` queues that need closed beads: `dev_no_pr`, `no_qa_record` and `finding_no_sanity`, and the `--days` look-back they used. The sanity and QA beads poured with each group make these states unreachable through the formulas.

## [0.14.1] - 2026-10-14

### Fixed
- `jev_client.py --assignment` can carry evidence that lives outside the repository files (randlee/atm-bd-orchestration#124). A sanity assignment may list `external_evidence` references: `{"kind": "bead_notes", "bead": <id>}`, `{"kind": "pr_body", "repo": "owner/name", "pr": <n>}` or `{"kind": "pr_comment", "repo": "owner/name", "comment_id": <n>}`. The client fetches each one itself (`bd show --json` or `gh api`, from `worktree_path`) and sends it in `state.external_evidence` together with its source (`kind`, `ref`, `url`, `updated_at`, `author`), so the receipt's `request_sha256` also covers it. Before this, the request carried only the deliverable text, the changed-file diff and `context` files, so an evidence-only deliverable was judged without its evidence. A malformed reference or a fetch that fails is a named `VALIDATION.INPUT` error with its fix, before any Jev call. An oversized request that carries external evidence is asked to be rebuilt, not split.
- `bead-queues` leaves `anchor`-labelled reference beads out of the ready queues (`bd ready --exclude-label anchor`). They stay open for provenance and still block the work that depends on them (#123).
- `sanity-split` adds the checked bead's notes (when it has any) and, with `--layer-pr`, the checked PR's body to every assignment's `external_evidence`. `dev-sanity-assignment.json.j2` 1.2.0 renders the field (default `[]`).

## [0.14.0] - 2026-10-11

### Changed
- sc-compose 1.7.0: the CLI and the Python wheel are pinned at `sc-compose == 1.7.0` (`manifest.yaml`, CI).
- `bead-groups` attaches each group with the real `sc-compose bead attach` (`bead preview-attach` under `--validate`) and passes its edges as the request's `relations`. `scripts/sc-compose-pour-mock` and the post-pour edge step are deleted. The beads, fields and edges poured are the same as 0.13.1 on the same input; only the provenance metadata key changes, from `sc_compose_attach` to sc-compose's `sc_compose_graph`.
- A relation's bead must exist when its group is attached, so `bead-groups` attaches sprints in dependency order (a predecessor named in the same run first), then findings. Under `--validate`, a relation to a bead that does not exist yet is reported as `missing bead <id> for <relation>`.
- Refusals are sc-compose's typed codes: `BEADS_GRAPH_CONFLICT` (a bead at a poured id from another formula revision, was `BEADS_ATTACH_CONFLICT`), `BEADS_GRAPH_EDGE_CONFLICT` (an edge that exists with another type, was `BEAD_GROUPS_EDGE_REFUSED`) and `BEADS_GRAPH_RELATION_INVALID`. An edge removed from an attached group is now refused with `BEADS_GRAPH_EDGE_MISSING` (exit 2), and its message carries the `bd dep add` repair; under `--validate` it used to be a `missing edge` problem (exit 5).
- The bead formulas carry front matter (`required_variables`) like every other template and declare no Beads `vars`: every value is a compose variable. `bead-groups` no longer checks for unbound `{{{ ... }}}` variables itself: `sc-compose bead render` 1.7.0 refuses one (`BEADS_RENDER_FAILED`, `undefined variable: <name>` with the formula path), fixing randlee/sc-compose#783 there, and drops front matter from the rendered formula (the front-matter half of randlee/sc-compose#785). `tests/test_frontmatter.py` no longer expects the formulas to fail.
- The sc-compose requirement is a range, `sc-compose >= 1.7.0, < 2`, not a pin. The installer accepts any CLI and `python3` wheel in the range when they are the same version, so an upstream patch or minor release does not stop the install. It refuses a version below the floor, from 2 on, or a CLI and wheel that differ, with the upgrade command. `tests/test_sc_compose_version.py` checks the same range-and-equal rule and no longer requires the newest PyPI release; it now runs in CI as well. CI installs 1.7.0, the floor.
- `sanity-split --layer-pr` reads every layer's range from one `gh api repos/{owner}/{repo}/pulls?state=all --paginate` listing joined locally, instead of a `gh pr view` per layer. A layer PR missing from the listing is a named error (exit 3).
- The `atm-bd-orchestration` and `atm-beads` SKILL.md `version:` markers are the package version (0.14.0, was 0.7.3 and 0.3.5); a test fails when a SKILL.md marker differs from plugin.json.
- `tests/test_render_parity.py` compares only the `sc-compose render --strict` CLI and the wheel, and now runs in CI. It no longer runs `atm compose`: atm's strict and boolean rendering are tested in atm-core (randlee/atm-core#1751, #1753), so the package's tests no longer depend on atm's release. No test is an expected failure.
- `sanity-run-history` appends its row with `sc-compose render --strict --append` instead of its own `O_APPEND` write. A log whose last line has no newline is refused (`ERR_RENDER_APPEND_NO_FINAL_NEWLINE`) instead of being given a separator; the error says to append the newline and rerun.

### Fixed
- qa-template 4.18.0: with no `pr_number`, step a reads the PR head from `git rev-parse origin/<branch>` against `base` instead of rendering `gh pr view  --json` (which resolved the current branch's PR). A fix round saves each reviewer's reply verbatim to `<scratch>/<task>-<reviewer>-result.json` and passes that path to `fix-round-scope filter --result`; `rust-best-practices-agent` is given `practice_mode` = `all`, which its contract requires.
- `fix-round-scope` returns 2 with a named message, not a traceback, when `--result` or `--carried` cannot be read; the `--result` message says it is a file path, not inline JSON, and where to save the reply.
- plan-review-template 3.11.0: in a fix round, `ceremony-qa` gets `review_mode`, `worktree_path` and `review_targets` like round 1, and a reviewer given its contract's input takes that contract's key (`carry_forward_findings` for `ceremony-qa`). Step b names the plan file `<plans_dir>/phase-<phase>.jsonl`, the place `plans_dir` is used.
- `bead-groups` option A: a sprint whose predecessor was poured under option A has no predecessor sanity bead to block on, so its dev bead blocks on the predecessor's dev bead; under B and C it still blocks on the predecessor's initial sanity bead. Dependency order follows the plan file's `depends_on`. `bead-groups` reports an sc-compose usage error (unknown subcommand, an older CLI first on `PATH`) as a typed error instead of a traceback.
- task-refused 1.0.3: with a branch set, `## Machine Status (JSON)` starts its own line.
- dev-sanity.md: the run history is appended with `sc-compose render --strict --append`; selection statuses are `done`, `undone` or `cannot_run`; "notes" is the close vars' `notes_md`. sprint-report's manual render writes a variables JSON in the documented shape (the script prints only rendered text). The sprint-group relations file no longer claims an edge to the predecessor's sprint container. The post-mortem preparation prompt no longer asks for a `preparation-status.json` nothing reads.
- Examples: `dev-sanity-template-vars.json` and `dev-sanity-complete-vars.json` drop the assignment keys their templates never place (including `base_sha`, which held a branch name); `dev-sanity-assignment-vars.json` drops `acceptance_criteria` and `design`.

### Workarounds for open sc-compose defects
- `sc_render.py` keeps its own JSON check of `.json` templates: the wheel has no checked render (randlee/sc-compose#787). `tests/test_frontmatter.py` still ignores the Jinja keywords sc-compose 1.7.0 reports as undeclared tokens (randlee/sc-compose#784), and no template comment names a double-brace example, since `--strict` still reads tokens inside `{# #}` comments (randlee/sc-compose#785).
- The formulas' front matter is checked in CI for undeclared variables: `sc-compose bead render` 1.7.0 renders a `{{{ x }}}` the front matter does not declare when it is supplied, and `sc-compose validate` cannot read `{{{ }}}` (randlee/sc-compose#791). Until bead render enforces it, `tests/test_frontmatter.py` compares each formula's `{{{ }}}` and `{% %}` variables (outside `{# #}` comments) with its `required_variables` and `defaults`, and fails on a variable used but not declared or declared but not used, naming the variable and the file. `bead-groups` itself does not check this at attach time.
- `skills/atm-beads/tests/test_sc_render.py` fails if `sc_render.render` stops refusing a `.json` template whose output does not parse (randlee/sc-compose#787).

## [0.13.1] - 2026-10-11

### Fixed
- An oversized Jev request is rebuilt by the caller, not just stripped of files. A `context` entry may name a line range, `path:start-end` (e.g. `docs/x.md:120-180`): `jev_client.py --assignment` sends only those lines at the pinned commit, labelled with the entry in `state.context`; a plain `path` still sends the whole file, and a range outside the file is a `VALIDATION.INPUT` error naming the entry. The size error tells the caller to rebuild `context` (narrow entries to the line ranges the deliverable needs, drop what it does not need) and rerun; `context` is left empty only when nothing in it is needed, and then the client splits `changed_files` as before. dev-sanity's rerun context and `sanity-merge`'s rerun check accept the range form; dev-sanity, sc-sanity-jev and sc-sanity-llm say how to use it.
- The oversize error code is `SANITY.JEV_REQUEST_TOO_LARGE` (recoverable), not `SANITY.JEV_INCONCLUSIVE`: the request is the caller's to rebuild, not an inconclusive Jev result.

- Every handoff that sends fenced JSON matches its receiver. dev-sanity passes each manifest entry's inner `assignment` object (not the `{number, assignment}` wrapper) to sc-sanity-llm and sc-sanity-jev. `jev_client.py --assignment` and `fix-round-scope --result` read a ```json-fenced file as well as raw JSON; both used to fail on the fence their callers are told to send. qa-template step g's round-1 `blocking.json` shape is valid JSON that `bead-groups --findings` accepts.
- plan-review-template 3.10.0 requires `worktree_path` (a checkout of `integration_branch`). Each reviewer's assignment is rendered from its `<reviewer>-assignment.json.j2` (else the input its agent file names) and sent as a ```json fence; ceremony-qa now gets its required `worktree_path`, and ceremony-finding-screen gets `worktree_path`, `sprint_doc` and `findings`, as in qa-template step f.
- `scripts/tests/test_handoff_contracts.py` checks every package JSON handoff against its real receiver (fences parse, `.json.j2` templates render to valid JSON, reviewer outputs, failure envelopes, Jev receipts, `blocking.json` and workflow-issue pours on a real bd), each with a planted negative.
- The skill test suites pass when run from the package as well as installed: tests no longer depend on unrendered install-time values or an installed `.claude/skills` tree.
- Scripts and tests render templates with the sc-compose Python wheel (`skills/atm-beads/scripts/sc_render.py`), not by running the `sc-compose render` CLI. The template's own directory is the confining root, so a render no longer depends on the caller's working directory (`ERR_RESOLVE_NOT_FOUND: template path escapes configured roots`). A `.json` template's output must parse, as the CLI's checked render requires. `install.py`, `sanity-split`, `sanity-run-history` and `sprint-report` use it.
- Every render is strict; there is no lenient path. `sc_render.render` has no `strict` parameter, so a missing or undeclared variable always fails naming the variable and the template (`sprint-report` rendered leniently before). Every `sc-compose render` command in the skills and agent docs includes `--strict`. `skills/atm-beads/tests/test_sc_render.py` (run in CI) fails if a lenient path comes back.
- Front matter is each template's input schema. `optional_variables` is not an sc-compose key and declared nothing, so it is removed from the 23 templates that had it; every one of those variables already had its default in `defaults`. `tests/test_frontmatter.py` (run in CI) runs `sc-compose validate --strict` on every `*.j2` in the package and fails on `ERR_VAL_MISSING_FRONTMATTER` or `ERR_VAL_UNDECLARED_TOKEN`, ignoring only the Jinja keywords sc-compose 1.6.1 misreports (randlee/sc-compose#784). The two bead formulas are strict expected failures: `sc-compose bead render` 1.6.1 copies front matter into the rendered formula, which is then not TOML, so they cannot carry front matter yet (randlee/sc-compose#785).
- `bead-groups` refuses a formula that references a `{{{ ... }}}` compose variable it does not supply (`BEAD_GROUPS_COMPOSE_UNBOUND`, naming the variable and the formula path) before anything is rendered or poured. `sc-compose bead render` 1.6.1 renders an unbound compose variable empty and succeeds (randlee/sc-compose#783), so a `.atm-bd/formula/` override could pour empty fields. The check is removed once bead render is strict and the formulas carry front matter.
- Tree walks in `tests/test_install.py` and `tests/gen_manifest.py` skip `.sc-compose/` (gitignored render logs), so a local log no longer fails a test or enters the manifest.

### Changed
- The sc-compose CLI and the Python wheel are one pinned version, `sc-compose == 1.6.1` in `manifest.yaml` (`install.sc_compose_pin()`). Install fails, with the upgrade commands, when the CLI or the `python3` wheel is another version; CI installs both at the pin (Python 3.11, which sc-compose publishes wheels for).
- Two local-only tests (skipped when `CI` is set): `tests/test_sc_compose_version.py` fails unless the pin, the CLI and the `python3` wheel are all the newest sc-compose release on PyPI (it fails when PyPI cannot be read), and `tests/test_render_parity.py` requires `atm compose`, `sc-compose render --strict` and the wheel to render every template with an example vars file identically and to reject a variable the template was not given. Two cases are strict expected failures in `atm compose` 1.6.1: it renders a boolean as `False` where sc-compose renders `false` (randlee/atm-core#1751), and it renders a missing variable empty (randlee/atm-core#1752).
- dev-sanity step 3, its selection paragraph, sc-sanity-jev steps 3-4 and the lead's cron line are shorter, with the same requirements (prompt-rewrite-judge; Jev confirmed no requirement dropped).

## [0.13.0] - 2026-10-10

### Fixed
- `validate-plan --ci` fails on a tracked `<plans_dir>/phase-<x>.jsonl` with no tracked `.atm-bd/phase-<x>.toml` (#95), reporting `<plan>: no tracked .atm-bd/phase-<x>.toml; the planner commits it with the plan` (exit 5). It used to pass with "no tracked .atm-bd/phase-*.toml to check".
- A sanity request over Jev's 96000-byte cap is no longer INCONCLUSIVE (#94). With `context` empty, `jev_client.py --assignment` splits `changed_files` into groups whose requests each fit (a file by hunks, a hunk by lines) and asks Jev once per part. It returns every part's full result (label, `changed_files`, Jev response, own receipt) and no combined verdict. `sc-sanity-jev` returns the parts verbatim; when they disagree, dev-sanity triages them with background agents and records the result that stands as `data.jev_final` citing every part. `sanity-merge` verifies every part's receipt, rejects a part reused across deliverables, and checks the findings against that result; the run history logs each part's receipt. The size error tells the sender to drop `context` entries and resend; dev-sanity and sc-sanity-jev never fall back to the LLM reply for a size failure.
- `bead-queues` reads only the phase's beads: one `bd list --all -l phase-<x>` and `bd ready -l phase-<x>` instead of an unscoped `--all`, which read the whole database. A descendant of the root without the `phase-<x>` label is not read; every phase template and formula adds it. A root without the label exits 2.
- `bead-queues` stack rows work under gh-stack 0.1, which does not count the trunk as a stack member: `gh stack view --json` falls back to worktrees on open stacks' layer branches, top first.
- Every handoff sends what its receiver's fenced JSON input accepts. The dev-sanity rerun sends `context` as plain path strings (not `{path, why}`), and dev-sanity.md lists the exact selection-entry keys sanity-merge requires. `scripts/tests/test_handoff_contracts.py` checks each handoff against its receiver.

## [0.12.1] - 2026-10-10

### Changed
- The lead's `bead-queues` run is a cron, mandatory while phase work is under way: every 15 minutes, and the lead acts on every row, since it is the only report of what the loop missed. The cron is disabled when phase work is paused or the phase is complete. It replaces "on each Loop pass", which leads skipped.
- The dev, dev-fix and fix-assignment templates require `lint_command` and run it with `test_command` to zero failures, again after the stack-top rebase. A lint failure inherited from a lower layer is the dev's to fix on its own layer; sanity still FAILs on lint.

## [0.12.0] - 2026-10-10

### Changed
- The lead, not a script, decides who takes a bead (#98). The dev gate's difficulty check (gate 5, `DIFFICULTY_MISMATCH`) is removed from `assignment-gates.py dev` and the dev, dev-fix and fix-assignment templates; base is now check (5). `difficulty` is a minimum: the lead picks a dev at that tier, or one tier up when none is idle (fast to terra/sonnet), never far above it.
- `sprint-report --dispatch` recommends only idle roster members of type `dev` (`atm teams update-member <team> <member> --agent-type dev`), at the bead's tier, else one tier up; publisher, QA, sanity and lead members are never listed. Its header says when no roster member has type `dev`.

## [0.11.10] - 2026-10-10

### Fixed
- `bead-queues` no longer calls `gh pr list --state open --limit 1000`, which tripped GitHub's secondary rate limit and froze `gh stack` for every agent on the account. Stack rows now come from `gh stack view --json`, run in the worktree checked out on the phase integration branch; `gh api repos/{owner}/{repo}/stacks` is kept only to enumerate open stacks. A stack that cannot be read (no trunk worktree, a gh error, a view on another trunk) prints `stack: stack not checked: <reason>` and the rest of the report still prints, exit 0 (it used to exit 2).
- The install test's repo-string scan skips git-ignored `.sc-compose/` logs, which a local test run leaves behind with absolute home paths.

## [0.11.9] - 2026-10-07

### Fixed
- No append locks. Each QA metrics row (`qa-template.xml.j2` step j) is built into a variable and appended with one `printf ... >>`. Each `sanity-run-history` and `post_mortem_jev.py` row is one `os.write` on an `O_APPEND` descriptor. A single append write is atomic, so concurrent writers cannot interleave rows. The QA template's `mkdir` spin-lock is gone; it proceeded without the lock after 10 s of contention. So are the two `fcntl.flock`s and `sanity-run-history`'s `.lock` file. `sanity-run-history` still refuses a retry whose run/reviewer row differs from the logged one.

## [0.11.8] - 2026-10-07

### Fixed
- `bead-queues` no longer needs `ATM_IDENTITY` or `ATM_TEAM`. It picks the ATM caller from `--as`, then `$ATM_IDENTITY`, then the repository's `lead` in `.claude/project/atm-bd-orchestration.yaml`. It picks the team from `--team`, then `$ATM_TEAM`, then `[atm].default_team` in the repository's `.atm.toml`. With neither an identity nor a configured lead, it exits 3 and says to pass `--as`, set `ATM_IDENTITY`, or set `roles.lead` and rerun the installer. Before, it refused outright without `ATM_IDENTITY` (the cron and the lead's monitor shell often have neither variable set).

## [0.11.7] - 2026-10-07

### Fixed
- The real-bd tests' dolt server can no longer outlive a killed test run. `start_server` now runs dolt under a small supervisor that stops it when the process that started it is gone, so a SIGKILLed pytest, which never reaches class cleanup, no longer leaves a server behind. It no longer starts a new session, so Ctrl-C reaches the server too. `stop_server` reads untruncated `ps` output. A new test SIGKILLs a process that started a server and asserts the server and its port are gone; against 0.11.6's `start_server` it fails with the server still running.

## [0.11.6] - 2026-10-07

### Fixed
- The real-bd pour and bead-queues tests start their own `dolt sql-server` on a free loopback port and run `bd init --server --external` against it, the mode consuming repositories run in, instead of `--proxied-server`. The proxy stops and restarts its dolt child, and the restart could find the old child still on the port ("Port 46151 already in use"), failing `bd init` or a later `bd create` with `ping db: invalid connection` (#88; CI runs 37573955297 and 37412959658). `bd cook --dry-run` now really runs in these tests instead of being skipped as `proxy.formula.unsupported`.

## [0.11.5] - 2026-10-06

### Fixed
- `atm-bd-orchestration` SKILL.md (0.7.3): the plan-review and phase-end review beads are created with `--id`, then attached with `bd update <id> --parent <root>`; bd 1.3.1 refuses `bd create` with both `--id` and `--parent` ("cannot specify both --id and --parent flags"), reported by team-lead@sc-compose on phase t's Plan Gate.

## [0.11.4] - 2026-10-06

### Fixed
- A request over the Jev budget now tells the agent what to do: the `SANITY.JEV_INCONCLUSIVE` envelope is `recoverable` and its `suggested_action` gives the request size and says to remove `context` entries (largest first) and rerun `--assignment`, never the deliverable text or `changed_files`. Every other client error with a known fix returns its steps too (fetch for unreadable commits, one retry after 60 s for connection, 429 and 5xx failures, rewrite an unreadable assignment file); the rest say to report the error unchanged. `sc-sanity-jev` (0.8.2) does what `suggested_action` says before returning an error. Before, the size refusal read as an outage and every slot fell back to LLM-only.

## [0.11.3] - 2026-10-06

### Fixed
- `jev_client.py` request cap 24000 -> 96000 bytes, Jev's documented 32k-token state budget (64k per request) at about 3 bytes per token; `judge.py` follows. The old 24000-byte "pilot bound" (~6k tokens) predates 0.10.4, which sends the real diff and context files, so a multi-file bead exceeded it: sc-obs obs-f-8.group-sanity lost all 7 Jev slots (`SANITY.JEV_INCONCLUSIVE`, recorded `CANNOT_RUN`) and went LLM-only. A request over the budget still refuses with a typed error and no Jev call.

## [0.11.2] - 2026-10-06

### Fixed
- Lead Role Loop: the stopped-assignee message puts the checklist outside the worktree's tracked tree, as dev-template step d already says. sc-obs cobs2 wrote `.obs-f-3.group-dev-checklist.md` untracked in its sprint worktree and dev-sanity refused the dirty worktree.

## [0.11.1] - 2026-10-06

### Changed
- Lead Role Loop: when an assignee stops on an active task (acting as if pair-programming, stating its next step and waiting for a user who does not exist: a turn ending on a promise of future work, status-only replies to reminders, `lead_notified` mail, or a `bead-queues` `task_stalled` row: an active task with 3 or more reminders, which `--json` now reports), the lead re-engages on the first sign with the checklist message instead of waiting for the reminder budget. `dev-template` (3.14.1): no user approves a step; never end a turn on a promise of future work while the task is active. sc-obs obs-f-3/obs-f-4 sat idle 7h and 4.5h after status-only replies to nine reminders (#81).

## [0.11.0] - 2026-10-06

### Added
- `.claude/skills/atm-bd-orchestration/scripts/bead-queues` (`.claude/skills/atm-bd-orchestration/scripts/tests/test_bead_queues.py`), ported from sc-observability `scripts/bead_queues.py` @ e1866dca (#55): read-only oversight of one phase root's descendants in the 0.9+ bead model (plan review, dev, sanity, QA, fix groups, finding beads, sanity-FAIL children, closable containers), joined with live ATM tasks and the gh stack (dev complete with no `PR #<n>` recorded, phase PRs on no open stack, an open stack that is not coherent or not one merge). `--json` follows the Hermes cron contract with edge-triggered rows (bead/PR id + queue) kept in `.atm-bd/bead-queues/<root>.json` and a `--min-age` window. The lead runs it on each Loop pass.

## [0.10.5] - 2026-10-06

### Changed
- The Jev transport ships inside the skill that uses it, `.claude/skills/atm-bd-orchestration/scripts/jev_client.py`; nothing is installed at the repository root. An upgrade removes the `<repo>/scripts/jev_client.py` an earlier version placed when unchanged and refuses it, naming it, when modified (`--overwrite` moves it to `.backup/`). Every script path in the agents, skills, references and templates is written from the repository root (`dev-sanity` 2.16.3, `sc-sanity-jev` 0.8.1, `atm-bd-orchestration` 0.6.21, `atm-beads` 0.3.5, `dev-sanity-template` 2.17.1).

## [0.10.4] - 2026-10-06

### Fixed
- `jev_client.py --assignment <file>` builds the sanity request from the assignment: the deliverable text verbatim, `git diff base_sha...commit` of `changed_files`, and the `context` files at `commit`; `sc-sanity-jev` (0.8.0) writes the assignment unchanged and runs it, never a request of its own. sc-obs `obs-f-2.f2-imp-006` got `SANITY.RESULT_INVALID` twice: the agent-written requests held a paraphrased deliverable and no code (`"evidence": "commit f8b8d2db; sync_http/tests.rs"`), so Jev answered near a coin flip (yes 0.49 / 0.37).

## [0.10.3] - 2026-10-06

### Fixed
- Jev children: `sc-sanity-jev` (0.7.1) says Jev is reached only by `python3 scripts/jev_client.py --request <file>`, with no tool or executable to look for, and is unavailable only when that command exits 2; `dev-sanity` (2.16.2) gives a child spawned without an agent type the full text of its agent file. sc-obs Codex sanity children, spawned untyped, looked for a Jev tool and `command -v jev` and reported Jev unavailable.
- The pour tests' failure messages carry the proxied server's `proxy.log` and `server.log` tails; a macOS CI run lost a `bd create` connection mid-pour (`unexpected EOF`, `ping db: invalid connection`) with no server evidence kept.

## [0.10.2] - 2026-10-06

### Changed
- Plan review runs `ceremony-qa` on every piped sprint container file and the root's file (`plan-review-template` 3.9.0).

### Added
- `agents/ceremony-qa.md` 0.2.0, from the atm-core and sc-observability copies (0.1.0): a new `unrequested_ceremony_adr` finding (important) rejects an ADR the plan adds or changes that institutes a rule, constraint, gate, check, artifact or review step the user did not explicitly request (e.g. a crate count, an API shape fixed in the ADR). A repository's own `.claude/agents/ceremony-qa.md` is moved aside by `install.py --overwrite`.
- `install.py --overwrite`: a modified or foreign file at a shipped path, or a modified file the version no longer ships, is warned about and moved to `<repo>/.backup/<UTC time>/<path>` (git-ignored) instead of failing the install; without it the failure names each file and the flag. Every consumer has its own copies of some shipped agents.

## [0.10.1] - 2026-10-06

### Fixed
- `bead-groups` pour renders each formula to `.atm-bd/pour/<target>.<ref>.<formula>.formula.toml`, beside its request and receipt, instead of bd's `.beads/formulas` registry. sc-compose refused the registry path on a fresh `.beads` (`BEADS_TEMPLATE_PATH_INVALID`, found by the sc-obs Phase F pour), and sprint-specific formulas left there were listed by `bd formula list` and could be poured by name with stale values. The attach reads the formula by path, so `sc-compose-pour-mock` drops its active-registry check.

## [0.10.0] - 2026-10-05

### Added
- `scripts/bv-analyze` and `references/bv.md`: the lead's read-only BV graph analysis of a fresh `bd --readonly export` (or a rendered plan file before import), fail-closed on any incomplete or stale load. bd 1.3 memory rows are dropped (raw export in `export.jsonl`, issues in `issues.jsonl`); `--target`'s blocker chain runs on the target and everything it transitively waits on (`prerequisites.jsonl`), so an unrelated bad row cannot fail it; a partial load names the rejected bead ids and BV's warnings. `bv.md` says when the lead runs it and what it decides: plan shape only before import, only added edges in motion, priority with a reason, staffing to the user (`SKILL.md` 0.6.20 links it from Lead Role).
- `validate-plan --ci`: offline (no bd, no fetch), every git-tracked `.atm-bd/phase-*.toml` loads and its plan file parses; one line per problem, exit 5 on problems, 0 when there are none or no phase file is tracked (`atm-beads` 0.3.4).

### Fixed
- `assignment-gates.py sanity`: `bd history` output that is not a JSON list of snapshots each with an `Issue` object is `GATE_CANNOT_RUN` with the output in the message on stderr, no longer an empty history and `READY`; every `GATE_CANNOT_RUN` from `evaluate` now prints its reason to stderr.
- `assignment-gates.py dev`: `PLAN_INVALID` (validate-plan exit 5) prints validate-plan's problems to stderr, and any other validate-plan failure (exit 2, e.g. no `.atm-bd/<phase>.toml`) is `GATE_CANNOT_RUN` with its message, no longer a silent `PLAN_INVALID`.
- `validate-plan --ci` runs without pydantic (CI runners have none): `bead_schema` is imported only by the bead checks.
- Example vars files no longer bake the installer's absolute path into the consuming repo: `primary_checkout` examples read `/path/to/<repo_name>`, and the unused `repo_root` install placeholder is gone.

## [0.9.0] - 2026-10-04

### Added
- Optional parallax mode: `agents/parallax.md`, a work-orchestrator teammate
  that runs the lead's routine orchestration under the same skill, so the
  lead keeps `bv`, monitoring and rulings and receives only its summaries and
  escalations. No formula or script changes.
- `prompt-rewrite-judge` skill: fresh subagents rewrite a prompt, `scripts/judge.py`
  asks Jev (through `scripts/jev_client.py`) one keep question per requirement plus
  `changes_rule`, `unnecessary`, `ambiguous` and `clearer_than_original`; planted
  controls must be caught before a winner is picked.

### Changed
- Stack #66 review fixes: every fallback use is logged with its verbatim error; a failed `judge.py` row carries the Jev client's exit code, JSON error, stdout and stderr verbatim; the dev-sanity probe-failed JEV slot carries the probe's own code and message; `plan_docs` are the piped sprint containers; `importing-md-plan.md` names the sanity bead `<id>.group-sanity` and maps `depends_on` (`SKILL.md` 0.6.19, `dev-sanity` 2.16.1, `plan-scope-reviewer-assignment` 1.1.2, `atm-beads` 0.3.3).
- Jev-judged prompt rewrite of `plan-review-template` (3.8.0): the fix-verification precedence and steps a, b, c, e and f say the same requirements in fewer or clearer words; no rule changed.
- Prompt rewrite with the Jev judge: `dev-sanity-template` steps a, a1, b, e, e1, e2 and e3 tightened with no requirement dropped (`dev-sanity-template` 2.17.0).
- A failed quick-fix QA gets one more quick-fix QA bead once every finding from it has closed, not one per finding, and the fix-complete `fixed` and `not_reproducible` Loop rows say so for a quick-fix finding (`SKILL.md` 0.6.18).
- Prompt rewrite with the Jev judge: tighter wording in the quality-mgr QA and phase-review steps, every requirement kept (`qa-template` 4.16.0, `review-template` 3.8.0).
- Prose steps of the developer templates rewritten through the `prompt-rewrite-judge` method (three subagent rewrites per step, Jev judged with planted controls caught, a winner applied only with no dropped requirement and no rule change): shorter, clearer wording, no requirement removed (`dev-template` 3.14.0, `dev-fix` 1.13.0, `fix-assignment` 3.15.0).
- A finding from a quick-fix QA has its own Loop row (no sanity bead, quick-fix re-QA) so a waiting one is never dispatched as an ordinary finding; the lead acts on its subagent's report for fix-assignment steps c and f1; a QA bead whose checked bead changes after sanity PASS is held by the new fix bead's sanity bead, never by reopening the passed one (`SKILL.md` 0.6.17).
- Review of the blocked-task refusal: the plan file is the minimum set of sprint edges and only the lead adds one; QA checks `bd ready` before claiming and refuses a bead that is not ready; a mid-task blocker is work another bead or agent owns that is not done, while a shared change the dev can make itself stays a Parallel Quick Fix; an edge bd refuses (one type per bead pair, no parent-child edge) goes to an open bead the blocker's work goes through; a refused task is re-assigned only once `bd ready` lists its bead, and a blocker that is not a bead gets one first; dev-sanity refuses a not-ready bead and a mid-task blocker like its template; with every roster agent busy the lead takes a quick-fix finding itself when its model fits its `difficulty`, a background subagent doing the fix steps (`qa-template` 4.15.0, `dev-sanity-template` 2.16.0, `dev-template` 3.13.0, `dev-fix` 1.12.0, `fix-assignment` 3.14.0, `dev-sanity` 2.16.0, `SKILL.md` 0.6.16).
- A dependency discovered in motion (a blocker refusal names it) is added by the lead with `bd dep add`, sprint beads included, never a replan; the sprint set stays frozen and no planned edge is removed (`SKILL.md` 0.6.15).
- Review round 1: a failed quick-fix QA pours nothing and lists its blocking findings in the close, and the lead re-dispatches the finder with the template the quick fix used on the same branch (a new layer from the top when anything is linked above it), then one more QA bead; the lead re-tests an open outage class bead's cause each Loop pass and closes it on success; `jev_client.py --announce --error <code: message>` announces a JEV child failure verbatim without probing, once per cause through `<workflow_issues_root>-jev-child-outage` (`SANITY.JEV_UNAVAILABLE`) or `-jev-result-invalid` (`SANITY.RESULT_INVALID`), each closed only by a later passing JEV child; a lead-repair sanity re-dispatch carries the new `branch`; `assignment-gates.py stack-top` follows an unlinked chain from layer 0 to its top, `STACK_AMBIGUOUS` when it branches (`qa-template` 4.13.0, `dev-sanity` 2.14.0, `SKILL.md` 0.6.10).
- The lead assigns a bead only while `bd ready` lists it, and orders and holds work only with bead edges, gates and `atm task move`, never by telling an agent not to run a task in its queue (`SKILL.md` 0.6.11).
- A blocked task is refused, never held: an assignee whose bead is not ready, or who meets a blocker mid-task, leaves the bead open and unassigned and closes the task `refused` naming the blocker and the dependency to add (`bd dep add <bead> --blocked-by <blocker>`); the lead adds it and re-assigns the same task once `bd ready` lists the bead (`dev-template` 3.12.0, `dev-fix` 1.11.0, `fix-assignment` 3.13.0, `dev-sanity-template` 2.15.0, `review-template` 3.7.0, `plan-review-template` 3.7.0, `qa-template` 4.14.0, `SKILL.md` 0.6.14).
- A failed quick-fix QA's finding beads go to an idle roster agent (the finder when idle, else a background developer subagent), not into a mid-task finder's queue (`SKILL.md` 0.6.13).
- Review round 2: the lead files each blocking finding of a failed quick-fix QA as a finding bead (`qa_bead` = that QA bead) and dispatches it to the finder with `fix-assignment.xml.j2`, no sanity bead, on the same fix branch (keeping its PR) or a new layer from the top, then one more quick-fix QA bead; a passing Loop re-test probe also closes a JEV-child-opened Jev class bead when no sanity task is ready or open; a workflow class bead's `remedy` holds the command that re-tests its cause (`fix-assignment` 3.12.0, `dev-sanity` 2.15.0, `SKILL.md` 0.6.12).
- The lead creates an important or minor finding's sanity bead and QA bead when it assigns the finding, in one `bd import` (finding <- sanity <- qa, both under the finding's parent: bd keeps one edge type per bead pair and refuses to close a bead with an open child), instead of after fix-complete and sanity PASS, so they are never dropped; a minor finding left in the backlog gets neither until assigned; `qa-bead.json.j2` 1.3.0 adds `parent` and `blocked_by` and leaves `layer`, `branch` and `commit` empty until dispatch; a `not_reproducible` finding has its group closed like a poured fix (`dev-sanity-bead.json.j2` 0.3.0, `fix-assignment` 3.11.0, `SKILL.md` 0.6.9).
- Jev replies carry proof of the call: `sc-sanity-jev` asks Jev for every deliverable and returns `data.jev` = the receipt `jev_client.py --request` prints (model, question, choice, probabilities, response id, request hash and a MAC keyed with `TYPESAFE_API_KEY`); `sanity-merge --reviewer sanity-jev` turns a reply with no valid receipt, a choice that contradicts its findings, a reused receipt, or a `SANITY.JEV_*` failure whose message is not the client's own text into a `SANITY.RESULT_INVALID` failure for that deliverable (kept in `rejected_results`), so selection falls back to the LLM reply, logged and announced; the selected merge rejects such a reply; each ledger row carries `jev_receipts` (`sanity-run-record` 4.0.0, required on a sanity-jev PASS/FAIL row, empty on sanity-llm, absent on older rows) (`sc-sanity-jev` 0.7.0, `dev-sanity` 2.13.0).
- Workflow gaps: `sanity-run-history` logs a CANNOT_RUN row even when a failure envelope is malformed (`deliverable` null when absent; an envelope without a well-typed code, message and recoverable is not a slot error) and a retry of a row logged before `errors` existed is the same row; `sanity-merge` rejects a failed rerun reply while either original reply is valid; `sanity-split` and `sanity-merge` ignore `.beads.gate.lock` and `.sc-compose/` like the gates; `SANITY_FROZEN` counts only a `bd history` snapshot closed with reason `PASS at `, and `assignment-gates.py sanity` runs git in `--worktree` and checks `--base`; the lead records each linked layer PR on the bead; QA names where its `pr_target` lower bound comes from; fix and dev-fix pre-claim only fetch and check ancestry (the branch is cut from the top); every close runs `bd close` first and closes the task only if it succeeded; a Loop row re-assigns a task whose assignee stays silent past the re-nudge; parallax escalates anything the lead keeps (`dev-sanity` 2.12.0, `dev-sanity-template` 2.14.0, `dev-template` 3.11.0, `fix-assignment` 3.10.0, `dev-fix` 1.10.0, `qa-template` 4.12.0, `plan-review-template` 3.6.0, `review-template` 3.6.0, `sc-sanity-jev` 0.6.1, `parallax` 0.1.1, `SKILL.md` 0.6.8).
- Serious failures are announced once per cause through the cause's workflow class bead (announce on create, append later uses, close when it clears), with the escalation-recipient lookup in one place (SKILL.md Lead Role) that the dev, fix, dev-fix, QA and plan-review refusal steps cite; `jev_client.py --startup` announces only with `--announce` (to the escalation recipients, else `--lead` saying none is set); dev-sanity enters probe-failed mode on probe exit 2 or a JEV child `SANITY.JEV_UNAVAILABLE`, re-probes at each task and closes `<workflow_issues_root>-jev-outage` on a pass; a failing gate command is `GATE_CANNOT_RUN`, not `SANITY.NOT_STACKED`; an outage-caused cannot-run waits for its class bead to close; `REVIEW_PENDING_JEV` carries the code findings and `post_mortem_jev` in the refusal notes and `check-review-completion.py` rejects an `unavailable` completion (`dev-sanity` 2.11.0, `dev-sanity-template` 2.13.0, `dev-template` 3.10.0, `fix-assignment` 3.9.0, `dev-fix` 1.9.0, `qa-template` 4.11.0, `plan-review-template` 3.5.0, `review-template` 3.5.0, `task-refused` 1.0.2, `SKILL.md` 0.6.7).
- A Parallel Quick Fix is a new layer cut from the stack top with `pr_target` = the lowest branch it needs, which its QA bead carries (`qa-bead.json.j2` 1.2.0) so the QA base check passes; a failed quick-fix QA re-dispatches the finder on the same branch, then one more QA bead; a `not_reproducible` poured fix has its group's sanity and QA beads closed with a reason; dev, fix and dev-fix assignments stop for a shared change and follow the assigner's base (`dev-template` 3.9.0, `fix-assignment` 3.8.0, `dev-fix` 1.8.0).
- Rebasing happens before sanity or QA is assigned and is the dev's at dev-complete; the lead fixes only what the dev could not, in its own worktree, before dispatch, with the new commit/base/PR/worktree; a layer that passed QA or has layers above it is never rebased, and a rebase never re-dispatches QA or sanity on another layer (SKILL.md Stack Discipline, team-lead requirement 42).
- Layer 0 alone cannot form a gh stack: sanity accepts an unlinked PR on the trunk (layer 0 awaiting layer 1, still rebased), `stack-top` returns the one open unlinked PR based on the target so the next root sprint lands on layer 0, and the lead links layers 0 and 1 together when layer 1's PR opens (`dev-sanity-template` 2.12.0, `dev-sanity` 2.10.0).
- Sanity reads the plan of a poured bead: `sanity-split` takes `## Deliverables` and `owned_paths` from the sprint container `metadata.sprint_bead` when the dev bead lacks them; a poured fix bead's description carries `## Deliverables` = its remedy (finding-group formula v6); QA's governing requirements/ADRs and the dev file fence fall back to the sprint container (`qa-template` 4.10.0, `dev-template` 3.8.0).
- Every PR lands on the top of its stack: `metadata.pr_target` is a lower bound (the actual base is it or a descendant); the dev and fix dev rebase onto the stack's current top, open the PR against it and close with the `/sc-gh-stack-view` output; sanity refuses `SANITY.NOT_STACKED` in place of `SANITY.STALE_BASE`; a sanity refusal for no PR, not stacked or not rebased is the lead's to fix as stack writer, never the dev's. `assignment-gates.py` dev, sanity and qa checks are descendant checks (`git merge-base --is-ancestor`). `dev-template` 3.4.0, `fix-assignment` 3.4.0, `dev-fix` 1.4.0, `dev-complete` 2.2.0 and `fix-complete` 2.3.0 (`pr_number`, `pr_url`, `stack_view`; a "Stack issue" section unless COHERENT with LANDING ✅), `dev-sanity-template` 2.8.0, `qa-template` 4.7.0, `dev-sanity.md` 2.5.0, `SKILL.md` 0.6.0.
- Dev sanity treats LLM and Jev as redundant: one failed reviewer keeps its failure envelope and selection takes the other's valid reply (`sanity-merge` rejects a failed reply selected over a valid one); only a deliverable with no valid reply is CANNOT_RUN. Each sanity ledger row carries `errors`, every failed slot's error verbatim (`sanity-run-record` 3.1.0). SKILL.md routes serious infrastructure failures and every fallback use, once per cause, to ATM's escalation recipients (else the lead, saying none is set); dev-sanity Startup, the quality-mgr role, review step b1 and the post-mortem JEV workflow point to it.
- A Parallel Quick Fix's QA bead carries `quick_fix` true (`qa-bead.json.j2` 1.1.0) and its QA gate skips only the sanity-PASS check, since a quick fix has no sanity; the lead may step in at critical points, preferably through a background developer subagent, but lead work is never part of the original plan. `qa-template` 4.9.0, `SKILL.md` 0.6.4.
- Stack review round 3: `assignment-gates.py stack-top --pr-target <b>` prints the top from GitHub's stacks (an open stack based on `<b>` or with a PR from it; `<b>` when none) and refuses `STACK_AMBIGUOUS` or `GATE_CANNOT_RUN` instead of guessing; the dev, dev-fix and fix templates call it. After a dev-fix, sanity (`sanity-split --layer-pr`) and QA diff only the sprint's own layer PR ranges (`layer_prs`), replacing `diff_base`. `dev-template` 3.7.0, `fix-assignment` 3.7.0, `dev-fix` 1.7.0, `dev-sanity-template` 2.11.0, `qa-template` 4.8.0, `dev-sanity.md` 2.8.0, `SKILL.md` 0.6.3.
- Stack review round 2: stack membership (sanity gate) and the stack top (dev, dev-fix, fix) come from GitHub's stacks API (`gh api repos/{owner}/{repo}/stacks`), not `gh_stack_view.py`; a merged lower bound counts only when its merge commit is in the base; after a dev-fix the sanity `diff_base` and the QA `base` are the PR base of the sprint's first layer, whose branch the dev-fix records as `pr_target`. `dev-template` 3.6.0, `fix-assignment` 3.6.0, `dev-fix` 1.6.0, `dev-sanity-template` 2.10.0, `dev-sanity.md` 2.7.0, `SKILL.md` 0.6.2.
- Stack review fixes: the sanity stack check reads `gh_stack_view.py --json` (cross-worktree) instead of the checked worktree's `gh stack` tracking; a lower bound gone from origin holds when its PR merged; a sanity-FAIL fix (`dev-fix`) is a new layer above the frozen checked layer, whose branch becomes the bead's `pr_target`; the planned `pr_target` is the nearest `must_follow` prerequisite's branch (or the trunk), never a parallel sibling, and link time records only `layer`; `gh pr create` takes `--fill`; the top is found with `--trunk <trunk>`, exit 2 or no matching stack meaning the dispatched base. `dev-template` 3.5.0, `fix-assignment` 3.5.0, `dev-fix` 1.5.0, `dev-sanity-template` 2.9.0, `dev-sanity.md` 2.6.0, `SKILL.md` 0.6.1.
- The lead opens and stacks the PR at dev-complete (before sanity); dev-complete and fix-complete tell the task assigner to open and link the PR and assign the next check; sanity refuses `SANITY.NOT_REBASED` when the commit does not contain `origin/<pr_target>`.
- Closes and reports go to the task assigner: the dev, dev-fix, dev-sanity,
  fix, QA, plan-review and review templates drop the `lead` and `cc`
  variables and every named recipient (each template minor-bumped);
  `dev-sanity.md` 2.3.0 and `roles/quality-mgr.md` say "the task assigner".
  A test fails any template containing `to the lead`, `to lead`,
  `team-lead`, `{{ lead`, `{{ cc` or `atm send {{`.
- `atm-bd-orchestration/SKILL.md` 0.4.0: the parallax role row; the
  work-orchestrator may write the stack in the lead's place.
- Review round 1: the remaining report recipients and close readers in the
  templates (patch-bumped), references and requirements say "the task
  assigner"; `importing-md-plan.md` reports gaps to the plan's author only;
  `SKILL.md` 0.5.2 drops the false "only the original assigner can
  re-dispatch a closed task id" limit and has one stack writer at a time;
  `agents/parallax.md` 0.1.0 lists what stays with the lead and points to
  the Lead Role section for handover and the stack writer.

## [0.8.2] - 2026-10-03

### Changed
- Sanity ledger: two rows per run (`sanity-llm`, `sanity-jev`); the
  `sanity-selected` row and `Pick` column are removed (Rand 2026-10-03). The
  selection stays dev-sanity's verdict, carried as `final_verdict` on both rows.
- Console report is `tail -n 20 <ledger> | jq -s '{runs: .}' | sc-compose
  render`; rows carry `completed_local`. `sanity-run-history` drops
  `--output`, `--limit` and the `sanity-llm.jsonl` merge.
- dev-sanity reruns a failed child after fixing its assignment or context, and
  uses the original wording "a PR targeting neither `develop` nor
  `integrate/*`".

### Fixed
- `dev-sanity-template.xml.j2` 2.4.0: step a checks `bd ready -n 0 --json`
  before any claim and refuses a base mismatch as `SANITY.STALE_BASE`; step b
  appends `sanity-llm` then `sanity-jev` (no SEL row); e1 states the
  `clamp(parent priority - 1, P1, P4)` blocking priority; e3 only renders.
- `sanity-merge` parses fenced JSON reply strings, so replies are kept as
  received. `sanity-run-history` prints the ledger path, ignores
  `completed_local` on an identical retry and drops `--started-at`; the
  console command reads that path under `set -o pipefail; test -s "$log"`.
- A rerun's assignment is the manifest assignment with `context` set (`jq`);
  its reply replaces the failed envelope before the reviewer merge.
- `sanity-run-table.md.j2` 2.1.0 skips rows of other reviewers.
- Ledger and console tests run real fenced replies through sanity-split,
  sanity-merge (LLM, JEV, selected) and sanity-run-history.

## [0.8.1] - 2026-10-03

### Fixed
- `scripts/tests/test_bead_pour_mock.py` no longer imports pytest, so the
  installed suite runs with `python3 -m unittest discover` and only the README
  requirements (it failed to import there: errors=1). The module is plain
  unittest: one class, `unittest.skipUnless` when bd, dolt or sc-compose is
  missing, and the shared workspace built in `setUpClass` with class cleanups
  that stop the dolt server and remove the temp dir even when setup fails.
  Test cases and assertions are unchanged.

## [0.8.0] - 2026-10-03

### Added
- Bead groups poured from formulas, with a mock of the sc-compose beads
  attach-pour (sc-compose #551, #613; ADR-0021) until sc-compose ships it.
  Additive: no template, mandatory log or schema changes.
  - `formulas/sprint-group.formula.toml.j2`: dev <- sanity <- qa under the
    sprint container. `formulas/finding-group.formula.toml.j2`: for each
    blocking finding, fix <- sanity <- qa as siblings under the same sprint
    container (flat model, Rand 2026-10-03); the fix bead carries the finding.
    Each has a `.relations.json` with the attach ref and post-pour edges; the
    E1 option (A, B or C; default C) is data there. `formulas/README.md`
    documents shapes and closers. A same-named file in
    `<repo>/.atm-bd/formula/` overrides the package formula.
  - `scripts/sc-compose-pour-mock`: `bead {render,validate,preview-pour,pour}
    --request R.json [--json]` with the sc-compose request, receipt, envelope
    and exit codes; render and validate run the real sc-compose. Pours attach
    directly under an existing parent at stable ids `<parent>.<ref>-<step>`,
    create only what is missing and never edit an existing bead.
  - `scripts/bead-groups`: pours and relates `--sprint ID[,ID]`, `--phase P`
    or `--findings FILE` (one QA round's blocking findings on one sprint); adds
    `validates` and cross-sprint `blocks` (normal: predecessor's sanity bead;
    tight: its sprint container); fills in only what is missing; refuses a
    second dependency type on a pair, a non-blocking finding, and a finding
    pour after the filing QA bead closed; `--validate` reports without writing.
  - `scripts/tests/test_bead_pour_mock.py`: runs both against a real bd 1.3.0
    proxied-server database; skipped when bd, dolt or sc-compose is missing.
    CI installs dolt 2.3.1.
## [0.7.0] - 2026-10-03

### Changed
- One dev-sanity teammate (Rand ruling 2026-10-03): `agents/dev-sanity.md` is
  the only named teammate and the whole dev-sanity role in one agent prompt.
  It spawns `sc-sanity-llm` and `sc-sanity-jev` as subagents.
  - Removed `agents/dev-sanity-llm.md` and `agents/dev-sanity-jev.md` (0.6.x
    compatibility shims that only pointed at `dev-sanity.md`).
  - Removed `skills/atm-bd-orchestration/roles/dev-sanity.md`; its binding
    rules (who fills the role, concurrent tasks, pre-claim refusals, check
    contract, verdicts, finding children, round cap, console report) are
    merged into `agents/dev-sanity.md` (2.0.0), each duty once. That file is
    now rendered at install (`{{ repo_name }}`, `{{ dev_sanity_member }}`).
  - `agents/dev-sanity.md` regains the Jev startup probe
    (`scripts/jev_client.py --startup --lead <lead>`); a failed probe records
    every JEV slot as `SANITY.JEV_UNAVAILABLE` while the LLM subagent runs.
    `jev_client.py` startup messages no longer name `dev-sanity-llm`.
  - SKILL.md "Roles": the dev-sanity prompt is `.claude/agents/dev-sanity.md`;
    the role-switch message takes the role's prompt path. README,
    `atm-beads/resources/dev-sanity.md` and tests follow.

### Migration from 0.6.x
Rerun the install: it removes the two legacy agent files and the role sheet
when unchanged (a modified copy fails the install, named). Point the
dev-sanity member's `[startup.<member>]` prompt in `.atm.toml` at
`.claude/agents/dev-sanity.md`.

## [0.6.1] - 2026-10-03

### Changed
- `roles.dev-sanity` (`dev_sanity_member`) names a team member, which may have
  no agent file (atm-core's `atm-sanity`, a roster member running the
  dev-sanity directive). The installer no longer checks it against
  `.claude/agents/<name>.md`; `qa_member` and `reviewers_round1` keep the
  check. There is no ATM roster lookup (CI has no ATM). Rand ruling 2026-10-03.

### Migration from 0.6.0
Rerun the install.

## [0.6.0] - 2026-10-03

### Changed
- Dev sanity triages reviewer findings and disagreements before close
  (upstream sc-observability PR #966 at 05367233, unmerged; applied as close to
  verbatim as the package allows). Every sanity run executes both reviewers
  (`sanity-llm`, `sanity-jev`), then a `sanity-selected` merge picks one
  whole reply per deliverable from a strict selection array; only the selected
  result creates finding children (`sanity-create-findings --reviewer
  sc-sanity-selected`), and checker defects create none. `sanity-split` no
  longer takes `--reviewers`; its manifest always lists all three reviewers.
  Reviewer assignments carry a `context` array.
  - `dev-sanity-template.xml.j2` 2.2.0: `reviewers` is no longer a variable
    (upstream 1.9.0 removed it; the package's config-driven `lead`, `cc` and
    `lint_command` stay required). `dev-sanity-assignment.json.j2` 1.1.0,
    `dev-sanity-complete.md.j2` 1.2.0, `sanity-run-record.json.j2` 2.0.0,
    `sanity-run-table.md.j2` 1.5.0 (upstream's numbers; `sanity-run-record` is
    major for its new required variables, as upstream 05367233).
  - Sanity ledger (OTel log contract, upstream format): history is appended in
    order LLM, JEV, SEL with the selected `final_verdict` on every row; records
    add `final_verdict` and `selection` (null except on SEL rows); the run
    table adds `Pick` and `Match` columns.
  - `agents/dev-sanity.md`, `dev-sanity-llm.md`, `dev-sanity-jev.md`,
    `sc-sanity-llm.md`, `sc-sanity-jev.md` and `roles/dev-sanity.md` follow.
  - Tests: `scripts/tests/test_sanity_selected.py` (new); upstream's root
    `scripts/tests/test_sanity_{merge,run_history,split}.py` now ship beside
    the skill's `scripts/tests` with import paths adjusted.
- Fix verification wording from upstream #967, merged into #966 at 05367233
  (filing-reviewer-only fix verification, now with plan-review text). The
  package's 0.5.0 rule stays in force; upstream's wording replaces the
  package's where both say the same thing, and the package-only enforcement
  (`fix-round-scope` checks, plan finding lines naming their reviewer,
  plan-scope-reviewer `round_index` wiring) stays.
  - `qa-template.xml.j2` 4.2.0: in a fix verification (`carry_forward` set) steps
    f and g1 do not render; steps b, e, g and i take upstream's fix-verification
    text; stack-discipline adds upstream's fix-verification sentence. The fixer
    closes a carried finding; fix verification confirms it or reopens it and no
    longer closes it itself. A fix-round PASS is a PASS from the filing reviewer
    and every carried finding confirmed fixed and closed (no deliverable
    completion term). `<fix-verification-precedence>` names only the steps that
    render in a fix round (c, e, g, i). Required variables unchanged.
  - `plan-review-template.xml.j2` 3.2.0: every dev bead and the root are piped
    before the branch, and a fix round's filing reviewers read all of them
    (still locked to their own ids); step d does not render in a fix round;
    round 1's plan-scope-reviewer "runs in full"; `<fix-verification-precedence>`
    names steps c and e.
  - `plan-scope-reviewer-assignment.json.j2` 1.1.1: upstream's description.
  - `roles/quality-mgr.md`: upstream's fix-round paragraph under "Fix
    verification takes precedence" (QA no longer closes the original finding),
    upstream's carried-finding line, refusal paragraph and Findings scope line; `SKILL.md`: upstream's qa-complete row and plan-scope-reviewer row.

### Migration from 0.5.0
Rerun the install. A lead that passed `reviewers` to `dev-sanity-template.xml.j2`
drops it.

## [0.5.0] - 2026-10-03

Breaking: the configuration contract and `qa-template.xml.j2` required
variables changed.

### Changed
- Fix verification is filing-reviewer-only (ruling 2026-10-03; upstream
  sc-observability 18d7158f). A review of an assigned fix is not a sprint
  review, regardless of round number or inherited `review_mode`: only the agent
  that filed the carried finding (`metadata.reviewer`) is dispatched, locked to
  its `metadata.finding_ref` and acceptance criterion. It reports
  fixed/open/regressed for that finding and files no new findings; no automatic
  `req-qa`/`arch-qa`/`rust-qa-agent`, no `ceremony-finding-screen`, no sprint
  sweep. Required CI stays a separate merge requirement. On verified PASS,
  quality-mgr reconciles the original finding bead's closure, not only the QA
  task. This replaces the 0.2.3 fix-round rule.
  - `roles/quality-mgr.md` "Reviewers": the upstream "Fix verification takes
    precedence" text; sprint rounds 1–2 stay sprint reviews with
    `reviewers_round1`.
  - `qa-template.xml.j2` 4.0.0: `reviewers_fix_round` and
    `reviewers_scope_locked` are no longer variables; adds upstream's
    `<fix-verification-precedence>`. A fix verification is `carry_forward`
    set, whatever the round or branch; `round` above 1 or a `fix/` branch no
    longer makes one (the `FIX_ROUND_WITHOUT_CARRY_FORWARD` render guard is
    gone), so a sprint round 2 or a parallel quick fix, which has no original
    finding to verify, renders as a sprint review with `reviewers_round1`. Step g files no finding bead in a fix verification; step i's PASS
    is every carried finding verified fixed. The `.sc/qa-log` rows are
    unchanged (`tested` is still the carried finding_refs).
  - `scripts/fix-round-scope`: no longer reads the configuration. `owned`
    prints every carried finding's filing reviewer with its own ids (a carried
    finding without `metadata.reviewer`/`finding_ref` is an error); `check
    --carried --dispatch` exits 5 (`FIX_ROUND_DISPATCH_MISMATCH`) unless the
    dispatch set is exactly those reviewers; `filter` accepts any filing
    reviewer. `check --findings` is gone (a fix verification imports nothing).
  - `config/atm-bd-orchestration.yaml.j2` 1.0.0: `reviewers_fix_round` and
    `reviewers_scope_locked` removed; `reviewers_round1` is the only reviewer
    list.
- Plan-review fix rounds are filing-reviewer-only too (ruling 2026-10-03).
  From plan round 2 on, `req-qa` and `arch-qa` are not re-run and
  `plan-scope-reviewer` does not run in full: only the filing reviewer of each
  carried finding runs, locked to it, with no ceremony screen and no new
  findings; `validate-plan` still runs, like required CI.
  `plan-review-template.xml.j2` 3.0.0 (`reviewers_scope_locked` is no longer a
  variable) adds a `<fix-verification-precedence>` block. Plan findings stay
  report lines, not beads, and now name their filing reviewer:
  `<bead> <severity> <reviewer> <field>: <what is wrong>` (`validate-plan` for
  step b findings). `scripts/fix-round-scope --plan` reads the carried lines,
  and `check --plan --dispatch` refuses any set other than their filing
  reviewers (`validate-plan` itself is step b, never dispatched).
  `plan-scope-reviewer-assignment.json.j2` 1.1.0 (as atm-core 2676a514)
  locks a round after the first to the reviewer's own carried ids, sets
  `findings_scope_locked`, and refuses to render
  (`FIX_ROUND_SCOPE_LOCK_REQUIRED`) without them; `plan-review-template.xml.j2`
  3.0.1 step c passes it `round_index` and those ids.
- Orchestration refusals reuse workflow class beads (upstream
  sc-observability 87a26739, #954). The refusal paths of `dev-template.xml.j2`
  3.1.0, `dev-fix.xml.j2` 1.1.0, `fix-assignment.xml.j2` 3.1.0,
  `dev-sanity-template.xml.j2` 2.1.0, `qa-template.xml.j2` 4.0.0,
  `plan-review-template.xml.j2` 2.1.0 and `review-template.xml.j2` 3.1.0 no
  longer render and import a new `<task>-wf-<CODE>` bead per task: they append
  the task id, head, command and failure evidence to an existing workflow class
  bead for the same failure signature and cite it, or, when no class matches,
  report the signature to the lead for classification and cite that message.
  Required variables unchanged (minor bumps).
  The `roles/quality-mgr.md` and `roles/dev-sanity.md` refusal paragraphs
  say the same.

### Removed
- Source-repository text: the roster model names in
  `atm-beads/resources/dev-sanity.md`, "this phase-D run" in
  `references/post-mortem-jev.md`, "Phase D" in
  `blocking-findings-guidelines.md`, and the `omega-prime` decision owner in
  `blocking-findings-guidelines.md` and `SKILL.md` (now "the user or their
  delegate").

### Migration from 0.4.0
Rerun the install. `reviewers_fix_round` and `reviewers_scope_locked` are no
longer configuration variables: the installer reads only declared variables
from registry.yaml, so leftover keys there are ignored (delete them at
leisure), and the rendered `.claude/project/atm-bd-orchestration.yaml` no
longer carries them. `--set reviewers_fix_round=...` or
`--set reviewers_scope_locked=...` is now an install error (unknown `--set`
variable). A lead that dispatches `qa-template.xml.j2` or
`plan-review-template.xml.j2` with a var file built from `repo_config.py json`
still renders: the extra keys are ignored. Plan finding lines carried into a
round-2 plan review must name their reviewer; a line from a 0.4.0 round 1
report needs the reviewer added (its `reviewers_md` says which).

## [0.4.0] - 2026-10-02

Breaking: the install contract changed. Configuration is one rendered file, the
installer owns only what it recorded, and there are no defaults.

### Changed
- Configuration: the inputs are the `required_variables` of the new
  `config/atm-bd-orchestration.yaml.j2` (`bead_prefix`, `lead`,
  `dev_sanity_member`, `qa_member`, `worktree_base`, `test_command`,
  `lint_command`, `integration_branch_pattern`, `plans_dir`,
  `requirements_globs`, `adr_globs`, `policy_path`, `reviewers_round1`,
  `reviewers_fix_round`, `reviewers_scope_locked`), read from the consuming
  repository's `.claude/agents/registry.yaml` (top-level key of the same name;
  the three members from `roles.lead`, `roles.dev-sanity`,
  `roles.quality-mgr`) or `--set`. Install renders it with
  `sc-compose render --strict` into `.claude/project/atm-bd-orchestration.yaml`;
  a missing variable fails the install and is named with the registry key that
  sets it. Unknown `--set` keys are rejected.
- No defaults or guesses: `lead` no longer defaults to `team-lead`,
  `worktree_base` is no longer guessed, `bead_prefix` no longer falls back to
  `.beads/config.yaml`, and `team` (rendered nowhere) is no longer an input.
  `.atm.toml` is no longer read.
- The resolved `lead`, `dev_sanity_member` and `qa_member` are written into
  `roles:` of registry.yaml (the rest of the file is kept as written), so
  `resolve-role` works after any successful install.
- Ownership: `.claude/project/atm-bd-orchestration.lock.json` records the
  version and the sha256 of every file the install wrote (skills, agents,
  `scripts/jev_client.py`, the config file). A rerun replaces recorded files the
  user has not modified, removes unmodified files the new version no longer
  ships, and fails naming every modified file. It never writes over a file it
  does not own and fails when a target skill directory exists that it does not
  own. A failed install writes nothing. `--force`, `--print-vars`, `DROPPED` and
  `INVENTORY` are gone.
- Install fails unless `.beads/metadata.json` shows `dolt_mode: server`
  (`BEADS_NOT_SERVER_MODE`), and unless every agent named in `qa_member`,
  `dev_sanity_member` and the three reviewer lists has `.claude/agents/<name>.md`
  (in the repository or shipped by this package).
- `--dest` must be a repository's `.claude` or `.codex` directory.

### Added
- `config/legacy-owned.json` (generated by `tests/gen_legacy_owned.py`): the
  sha256 of every file 0.1.0-0.2.3 shipped, used once to migrate an install that
  has no lock file. Delete it once no repository carries a pre-0.4.0 install.
- `conftest.py` (test path setup; keeps pytest out of the uninstalled skill
  suites) and CI: `.github/workflows/tests.yml` runs the package tests on
  ubuntu and macos with sc-compose 1.6.1.

### Migration from 0.x
Add the variables above to `.claude/agents/registry.yaml` (the install error
lists every missing one), then rerun the install. The first 0.4.0 install finds
no lock file and adopts existing files whose bytes are what 0.4.0 installs or
what some 0.x version shipped; it fails, naming them, on any other file at a
path it ships, and on a skill directory with no such file. A rendered file (one
listed under `render:`) from 0.x is adopted only when it equals what 0.4.0
renders with the current values; if you did not edit a refused file, delete it
and rerun. A copy that was synced from upstream rather than installed by the
package matches no shipped bytes: remove the package's skill directories and
agents and install fresh.

## [0.2.3] - 2026-10-02

### Changed
- Fix rounds run `req-qa`, `arch-qa` and `rust-qa-agent`; `ruthless-boundary-qa`,
  `rust-best-practices-agent` and `rust-service-hardening-agent` only re-check
  their own carried finding ids, scope-locked (ruling 2026-10-02). A fix round is
  `carry_forward` set, `round` above 1, or a `fix/` branch. `qa-template.xml.j2`
  refuses `round` above 1 without `carry_forward` and gates the fix-round
  `bd import` on `scripts/fix-round-scope check`; `ruthless-boundary-qa-assignment.json.j2`
  requires `qa_round` and refuses a fix round without its own ids;
  `plan-review-template.xml.j2` passes `qa_round`.

### Added
- `scripts/fix-round-scope` (`owned`, `filter`, `check`) and its tests; render
  tests for the fix-round rules in `scripts/tests/test_templates.py`.

Package-only, pending upstream: listed in README.md. Builds on 0.2.2 (#10).

## [0.2.2] - 2026-10-02

### Fixed
- `atm-beads/templates/sprint-bead.json.j2` renders the `difficulty` that
  `SprintBead` requires and the `stage:sprint` label, so a strict render passes
  `validate-plan` check 2 (#8). Takes upstream PR #936 at d566149d verbatim ahead
  of its merge; listed under "Package-only changes pending upstream" in README.md.
- `tests/test_skill_suites.py` renders the template with both example vars in an
  installed copy and validates the metadata against `SprintMetadata`.

## [0.2.1] - 2026-10-02

### Changed
- `references/post-mortem-context-preparation.md` follows sc-observability PR #933
  head 07ad4d26 (supersedes b1ffa1ad): an oversized compound obligation may be
  decomposed into parent-mapped subpredicates. Upstream delta applied verbatim.

## [0.2.0] - 2026-10-02

### Changed
- Skills and agents re-mirrored from sc-observability `develop` at f2ebe1bc plus
  open PR #933 at b1ffa1ad: every in-scope file is the upstream file verbatim
  except repository-specific names and paths, replaced by install-time values or
  neutral examples. Skill, agent and template header versions are upstream's.
- The sprint index is the phase JSONL (`docs/plans/phase-<x>/sprints.jsonl`)
  validated in code and by `schemas/*.schema.json`; the sprint index schema asset
  is gone, as upstream deleted `docs/plans/sprints.schema.json`.

### Added
- `agents/dev-sanity.md`, sanity run history and record templates, the
  phase-end post-mortem with JEV screening and context collection
  (`references/post-mortem*.md`, `post_mortem_jev.py`, `post_mortem_context.py`,
  `check-review-completion.py`), `blocking-findings-guidelines.md`, the pydantic
  bead schemas (`atm-beads/scripts/bead_schema.py`, `schemas/`).
- `assets/scripts/jev_client.py`, placed at `<repo>/scripts/jev_client.py`, and its tests.
- `tests/test_skill_suites.py` runs the skills' suites in an installed copy.
- `prepare()` checks that `python3` imports pydantic and PyYAML.

### Removed
- `blocking-finding-gates.py`, `check-plan.jq`, `migrate-phase-contract`,
  `phase_contract_check.py`, `export-sprint-index`, `report-detailed.md.j2` and
  their tests and fixtures, removed upstream; `complete()` deletes them from an
  existing install.

## [0.1.0] - 2026-09-26

### Added
- First shared copy of the `atm-beads`, `atm-bd-orchestration` and `sprint-report`
  skills and the `dev-sanity-llm`, `sc-sanity-llm`, `dev-sanity-jev` and
  `sc-sanity-jev` agents, taken from sc-observability `develop` at 9ac5cd4
  (the merge of PRs #263, #262, #266 and #267: phase contract validator,
  assignment gates with enumerated refusal codes, blocking-finding R16 gates,
  epic-only top level with QA and finding beads under their sprint) with every
  repository- and team-specific string replaced by an install-time value (see
  `registry.yaml`).
- `assets/docs/plans/sprints.schema.json`, the sprint index schema; `install.py`
  also places it at `<repo>/docs/plans/sprints.schema.json` when missing.
- `install.py`: sc-install `prepare()`/`complete()`/`cleanup()` hook and standalone
  installer that renders the repository values with `sc-compose render --strict`.
- `templates/workflow-issue-bead.json.j2` takes `parent` (the repository's
  workflow-issues root bead) as a required variable instead of a hard-coded id.
