# Role Requirements

One file per agent, with its subagents and scripts: what it does, when, in what order. Each file has `Requirements` (checked against the package sources) and `Unresolved` (package sources that contradict each other). A prompt line that maps to no requirement goes to review: the requirement may be missing.

| File | Agent |
| --- | --- |
| [dev.md](dev.md) | dev, dev-fix, fix; `assignment-gates.py` |
| [dev-sanity.md](dev-sanity.md) | dev-sanity with sc-sanity-llm, sc-sanity-jev; sanity scripts |
| [quality-mgr.md](quality-mgr.md) | QA, plan review, phase-end review; reviewers, ceremony screen |
| [planner.md](planner.md) | design to beads; `validate-plan` |
| [team-lead.md](team-lead.md) | sequencer; swarm-master (none in the package) |
| [parallax.md](parallax.md) | work-orchestrator teammate (optional) |
| [task-close-hook.md](task-close-hook.md) | `atm-task-close.py`: the one way a dev, fix, sanity or qa task completes; mechanical sanity and qa assignment as the lead |
| [sanity-stack.md](sanity-stack.md) | sanity-stack subagent: links one next-top layer under the stack bead lock; the race, lean mode, lock timeout |
| [console.md](console.md) | `atm-bd console` and `atm-bd cron`: identity, time, log, GitHub polling, rows |

## Who does what

| Agent | Responsible for |
| --- | --- |
| **team-lead** | Runs the phase. Imports the plan into beads, gets it through plan review, dispatches every ready dev and fix bead as an ATM task (sanity and qa assignment is mechanical, done by the close hook as the lead), and owns the stack (unstack, restack, land, repair); layers are linked by the sanity side on PASS under the stack bead lock. Lands the stack, runs the phase-end review, and merges the phase. Makes the rulings and escalates to the user. |
| **parallax** (optional) | The lead's work-orchestrator. Runs the routine loop for the lead: dispatches ready beads, syncs, and lands the stack. Sends the lead only summaries and escalations. The lead keeps the graph analysis (`bv`), rulings, gates, phase closure and the merge. |
| **planner** | Turns a design into a phase plan in beads: a phase root, sprint containers, `owned_paths`, and the dependencies between sprints. Cuts sprints for the shortest critical path, and `validate-plan` must pass before import. |
| **dev** (dev, fix) | Implements one bead on its own branch with tests and opens a PR on the top of the stack. Works through a private checklist, one deliverable at a time, then runs the close hook, which verifies the PR, closes the bead and assigns sanity. Its task stays open, and it waits, until sanity passes; a sanity FAIL comes back as a `sanity-fail` message, and a lost race for the top as `rebase-required`, both fixed on the same branch. A fix bead does the same for one blocking finding. |
| **dev-sanity** | Checks a closed dev or fix bead at its pinned commit: is each numbered deliverable written, and does lint pass? Spawns one checker per deliverable (`sc-sanity-llm`, plus `sc-sanity-jev` when Jev is available) and merges their results. On PASS it links the layer under the stack bead lock (itself, or through sanity-stack when more than one sanity task is open), then its close hook closes the sanity bead, closes the dev's task and assigns QA; on FAIL it reopens the dev bead and sends the dev exactly what was NOT complete (`sanity-fail`), up to `max_sanity_rounds`, then escalates to the lead. Not QA: it never judges quality. |
| **sc-sanity-llm / sc-sanity-jev** | dev-sanity's subagents. Each answers "is this deliverable written?" for one deliverable, as JSON. They are read-only and make no bead writes. |
| **quality-mgr** | Runs QA on a bead whose sanity check passed. Fans out to the repository's round-1 reviewers in the background, re-verifies every cited line, and screens the findings for ceremony. Pours a fix, sanity and QA group for each blocking finding, and files important and minor findings as beads. In a fix round only the reviewer that filed a finding verifies its fix. Also runs plan review and the phase-end review. |
| **reviewers** (`reviewers_round1`, for example req-qa, arch-qa, rust-qa-agent) | quality-mgr's subagents. Each reviews the change from one angle and returns findings with file and line evidence. |
| **ceremony-finding-screen / ceremony-qa** | Flags findings (and, in plan review, plan content) whose remedy is a process artifact with no consumer, so it is closed instead of built. |

## Dev / fix loop (one sprint)

The lead (or parallax) dispatches the dev and fix beads that `bd ready` lists. The arrows into dev-sanity and quality-mgr are the close hook (`atm-task-close.py`) assigning the next bead as the lead the moment the one below it closes; bd's edges decide the order: sanity waits on dev, QA waits on sanity. The dev's task stays open until sanity passes.

```mermaid
flowchart TD
    L["team-lead: dispatch dev bead"] --> D["dev: implement + tests,<br/>PR on stack top, close hook --role dev<br/>(bead closed, task open, waits)"]
    D -->|"hook assigns sanity as the lead"| S["dev-sanity: is each deliverable written?"]
    S -->|"one per deliverable"| SC["sc-sanity-llm / sc-sanity-jev"]
    SC -->|"JSON verdicts"| S
    S -->|"hook FAIL: reopen dev bead,<br/>sanity-fail to the dev (configured cap)"| D
    S -->|"PASS: link under the stack bead lock<br/>(sanity-stack); lost the top: rebase-required"| D
    S -->|"stacked: hook closes sanity bead and dev task,<br/>assigns QA as the lead"| Q["quality-mgr: QA round 1"]
    Q -->|"fan-out, background"| R["reviewers_round1<br/>req-qa, arch-qa, rust-qa-agent, ..."]
    R -->|"findings with evidence"| CS["ceremony-finding-screen"]
    CS --> Q
    Q -->|"no blocking findings: PASS<br/>important/minor filed as beads"| Done["team-lead: land stack,<br/>close sprint container"]
    Q -->|"blocking: pour fix, sanity, qa group;<br/>lead dispatches the fix"| F["fix: one blocking finding,<br/>new layer on stack top, close hook --role fix"]
    F -->|"hook assigns sanity"| S2["dev-sanity on the fix"]
    S2 -->|"hook FAIL: reopen fix bead,<br/>sanity-fail"| F
    S2 -->|"PASS"| Q2["quality-mgr fix round:<br/>filing reviewer only"]
    Q2 -->|"not fixed"| F
    Q2 -->|"fixed"| Done
```

## Wave orchestration with parallax

The lead hands routine dispatch to parallax and keeps the plan gate, `bv`, rulings and phase closure. A dependent sprint's dev bead waits on its predecessor's **sanity** bead, not its QA. The next wave starts once the code is known to be written, while QA of the previous wave runs in parallel.

```mermaid
flowchart TD
    subgraph Lead["team-lead"]
        PG["plan gate: import, plan review,<br/>validate-plan"]
        BV["bv: waves, stalls, rulings"]
        PE["phase-end review,<br/>merge phase to base branch"]
    end

    subgraph PX["parallax: Loop"]
        RDY["bd ready + bead-queues"] --> DSP["dispatch every ready bead"]
        DSP --> RDY
        LAND["sync + land stack<br/>(only stack writer)"]
    end

    subgraph W1["wave 1 (parallel sprints)"]
        A1["sprint A: dev"] --> A2["sanity"] --> A3["QA"]
        B1["sprint B: dev"] --> B2["sanity"] --> B3["QA"]
        A3 -.->|"blocking finding"| AF["fix, sanity, QA group"]
    end

    subgraph W2["wave 2"]
        C1["sprint C: dev"] --> C2["sanity"] --> C3["QA"]
    end

    PG -->|"plan passes"| RDY
    DSP --> A1
    DSP --> B1
    A2 -->|"PASS releases"| C1
    B2 -->|"PASS releases"| C1
    DSP --> C1
    A3 --> LAND
    AF --> LAND
    B3 --> LAND
    C3 --> LAND
    PX -->|"SUMMARY / ESCALATION"| BV
    BV -->|"rulings"| PX
    LAND -->|"every sprint QA green"| PE
```
