# bd-cache: reads, journaled writes, drain, sync and outage

One SQLite file per dolt database, `.atm-bd/<dolt_database>.bd-cache.sqlite3`
(ignored), holds cached bead rows and a journal of bead writes. The close
hooks and close scripts read from it and write to its journal; only the drain
calls `bd` to apply a write. Every path ends applied, refused or escalated to
the phase lead. The contract is `scripts/BD-CACHE.md`.

## Write and drain

```mermaid
flowchart TB
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef script fill:#e6f4ea,stroke:#188038,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000
  classDef ok fill:#e6f4ea,stroke:#188038,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  CALLER["close hook or close script:<br/>write bead op args, precondition, idempotency key"]:::script
  WCHK{"valid op, key not reused for another write,<br/>precondition holds on the cached view?"}
  WREF["typed error to the caller, nothing journaled<br/>(PRECONDITION, IDEM_CONFLICT, BAD_ARGS)"]:::bad
  J[/"journal entry: pending<br/>the same key again returns it"/]:::file
  START["drain: started detached by the hook, at once by the<br/>close script, and by the bd-cache cron every tick"]:::script
  LOCK{"drain lock free?"}
  BUSY["busy: the lock holder drains"]:::ok
  PRE["backend check (bd version, bd schema),<br/>then one batched bd list --id of every pending bead"]:::script
  REACH{"bd answers?"}
  OUT["outage: recorded once with its start,<br/>no attempt counted, entries stay pending"]:::bad
  EACH["each pending entry in seq order"]
  ABSENT{"bead in the batch?"}
  ACH{"effect already in bd?"}
  PRECON{"precondition still holds?"}
  RUN["run bd close / update / dep add"]:::script
  RES{"exit"}
  RETRY["attempts + 1, stays pending,<br/>the bead's later entries held"]:::bad
  CAP{"attempts = 5?"}
  RB["read back with bd list --id, store the row,<br/>mark applied (stale if the read-back fails)"]:::ok
  APPLIED["applied, no second write"]:::ok
  ESC["escalated"]:::bad

  CALLER --> WCHK
  WCHK -- no --> WREF
  WCHK -- yes --> J --> START --> LOCK
  LOCK -- no --> BUSY
  LOCK -- yes --> PRE --> REACH
  REACH -- no --> OUT
  REACH -- yes --> EACH --> ABSENT
  ABSENT -- no --> ESC
  ABSENT -- yes --> ACH
  ACH -- yes --> APPLIED
  ACH -- no --> PRECON
  PRECON -- no --> ESC
  PRECON -- yes --> RUN --> RES
  RES -- "0" --> RB
  RES -- "13, bd guard refused" --> ESC
  RES -- "other failure or timeout" --> RETRY --> CAP
  CAP -- yes --> ESC
  CAP -- "no: next drain" --> START
```

A read (`get`) returns the cached row with that bead's pending entries
replayed on top; a miss makes one `bd list --id` call. Agents' own `bd show`
and `bd ready` read bd directly, so they see a journaled write only once the
drain applies it: under a second normally, at most one cron tick. For the
sanity hook the lag delays work and never releases it early.

## Journal entry states

```mermaid
stateDiagram-v2
  [*] --> pending: write
  pending --> pending: retry or outage
  pending --> applied: drain landed it, or already achieved
  pending --> escalated: absent, precondition unmet, exit 13, or 5 attempts
  escalated --> resolved: lead sets the bead, bd_cache.py resolve seq
  applied --> [*]
  resolved --> [*]
```

An escalated entry is no longer replayed on reads. `status` and the cron's
`doctor` list it until it is resolved. `prune --phase x` is refused while the
phase has pending or escalated entries.

## Cron tick, sync and outage

```mermaid
flowchart TB
  classDef file fill:#f1f3f4,stroke:#5f6368,stroke-dasharray:4 3,color:#000
  classDef script fill:#e6f4ea,stroke:#188038,color:#000
  classDef atm fill:#fef7e0,stroke:#f9ab00,color:#000
  classDef bad fill:#fce8e6,stroke:#c5221f,color:#000

  TOML[/".atm-bd/phase-*.toml: root, lead"/]:::file
  TICK["bd-cache cron tick, 25 s budget"]:::script
  DR["drain, deadline 5 s before the budget ends;<br/>an entry not started by then is deferred"]:::script
  ACT["active phases: root bead not closed"]:::script
  SYNC["sync each active phase:<br/>bd list -l phase-x --updated-after, then re-read stale rows"]:::script
  SF{"sync failed?"}
  UNR{"bd unreachable?"}
  SA["SYNC_FAILED to that phase's lead,<br/>once per cause; success clears it"]:::atm
  OA["outage to every active phase's lead, once;<br/>cleared by the next bd list that answers"]:::atm
  EA["each escalated entry not yet announced:<br/>to its phase's lead with the resolve command"]:::atm
  DEL{"every message delivered?"}
  REF["reply refused with the list; retried next tick"]:::bad

  TOML --> ACT
  TICK --> DR --> ACT --> SYNC --> SF
  SF -- yes --> UNR
  UNR -- yes --> OA
  UNR -- no --> SA
  SF -- no --> EA
  OA --> EA
  SA --> EA
  EA --> DEL
  DEL -- no --> REF
```

An outage counts no attempt, so a long bd stall escalates nothing by itself;
a reachable bd that refuses or fails a write still counts toward the five
attempts. A passing `bd version` does not clear an outage. Messages go to
`<lead>@<team>` from the cron, through `atm send`.
