# Console operational logs

## Dispatch verification

Ready sanity and QA assignments require a successful merge check for their
branch and target. Missing results, missing targets, timeouts, and unsupported
git commands hold assignment with an actionable problem until verification
succeeds. The persisted merge states remain `clean` (verified), `conflict`, and
`error` (verification unavailable); a failed probe replaces any cached clean
result. PR and CI status never hold assignment.

Each tick with a ready assignment candidate refreshes origin before pinning a
commit. A fetch or remote-ref read failure appears in the snapshot and logs,
holds assignment, and clears eligible cached refs so recovery retries safely.
`--no-fetch` explicitly skips refresh and permits the saved remote refs, while
still requiring a successful current merge check.

## Operational events

The console owns one `sc-observability` logger for its repository for the life
of the process. All ticks reuse it, including stopped and failed ticks. Logs
live under `.atm-bd/atm-bd-console/logs/`; the console sink is disabled. Each
tick flushes admitted events before saving its snapshot. Logger construction,
event admission and flush failures appear in the snapshot's Errors section
and in headless error output. They do not change whether a dispatch succeeded.
After repairing a logger construction failure, restart the process to retry
initialization.

Each proposed assignment, send or batch has an `action` event containing
`effect`, `recipient`, all `task_ids`, `outcome`, and the complete typed delivery
`error` (or null). Outcomes are `sent`, `failed <error kind>`, and `dry_run`.
Dry runs never deliver an ATM effect. Events cover every action even when the
dashboard's recent-action list retains only five. A logging error means the
operational log may be incomplete; use the snapshot's delivery outcomes and
Errors section to investigate. Source reads and git fetch/merge probes record
their operation, outcome and typed error, with team, task IDs or branch context
where available. `hold` events record the task, recipient and hold reason;
they do not introduce additional dispatch rules.

The phase's `.atm-bd/atm-bd-console/<root>/actions.jsonl` is an append-only
journal. Assignment/send entries record attempts, followed by a `hold` entry
on failure; `dry_run` entries record previews. An attempt entry alone is not
proof of delivery. Operational `action` events carry the explicit outcome.

## Retention and rotation

The console does not enforce an age or size retention limit on either the
operational log directory or `actions.jsonl`. Both require operator-managed
rotation. Check their disk usage daily; archive at least weekly or when either
reaches 100 MiB. Keep compressed archives for 30 days unless the project's
audit policy requires longer. These are maintenance recommendations, not
automatic limits.

To rotate safely:

1. Pause cron launches and stop every console using the repository. Logs are
   shared across its phases, so stopping just one phase is insufficient.
2. Move the entire `logs/` directory and each phase's `actions.jsonl` to a
   timestamped archive outside the runtime directory. Compress the archive and
   expire archives older than the chosen retention window.
3. Leave `state.json`, `snapshot.json`, `merge.json`, `prs.json`, and lock files
   in place. They are current runtime data, not log archives.
4. Restart the consoles and resume cron. The logger recreates its directory;
   the next action recreates `actions.jsonl`. Check the next snapshot for
   logging or journal write errors.

Do not truncate or rename active logs while writers are running. A scheduled
rotation job must coordinate the same stop/archive/restart sequence.
