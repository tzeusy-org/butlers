---
name: butler-relentless-jarvis-pursuit
description: >
  Recurring generative audit pursuing a world-class JARVIS-like system across the Butlers
  ecosystem: per-page /th-design UX audits plus ecosystem ideation lenses (new connectors,
  inference/model-routing flow, knowledge-graph growth, cross-butler interaction,
  proactivity), grounded in heart-and-soul; backward compatibility waived. Outputs a dated
  dossier under docs/redesigns/, an artifact report, and a gated beads epic. Also carries
  the QC counterpart as a subskill (subskills/ui-maturity-audit) verifying surfaces are real
  and wired, not skins over stubs. Triggers: "run the JARVIS pursuit", "deep-dive audit of
  the frontend ecosystem and UX", "generate new feature ideas for the butler ecosystem",
  "QC the dashboard", "is this flow actually wired", "is the X page real or just a skin",
  "did the redesign actually ship the behaviour". Not for spec-vs-code drift bead-filing
  (reconcile-spec-to-project) or single-component visual critique (impeccable).
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
  status: active
  last_reviewed: "2026-07-04"
compatibility: >
  Needs repo-root access, bd (beads) against the shared Dolt server, and the Workflow tool
  for the fan-out. The dev stack being up is optional (auditors work statically; live
  verification is a bonus).
---

# Butler Relentless JARVIS Pursuit

A **recurring, generative** audit. The goal is not to verify correctness (that is
the `ui-maturity-audit` subskill) but to relentlessly close the gap between what Butlers is today
and a truly world-class JARVIS-like system: the operator console for a sovereign, one-person
AI household staff. Each run produces *new* redesign moves and *new* ecosystem extensions —
never a re-filing of what a prior run already found.

Invoking this skill is the owner's explicit opt-in to multi-agent orchestration: run the
fan-out phases with the **Workflow tool**.

**Precedent:** the 2026-07-03 run (`docs/redesigns/2026-07-03-jarvis-audit.md` + `-data.json`,
epic `bu-86c4c`, 28 subagents). Reuse its shape; treat its dossier as the canonical example of
the deliverable and the primary dedup source.

## Routing: pursuit vs. QC

Two modes live under this skill. Load **one** body per task:

| Ask | Mode | Load |
|---|---|---|
| Generate new redesign moves / feature ideas; "run the JARVIS pursuit"; deep-dive audit toward the ideal | **Pursuit** (generative) | the rest of this file |
| "Is this flow actually wired", "QC the dashboard", "is the page real or a skin", "did the redesign ship the behaviour" | **QC** (verification) | [subskills/ui-maturity-audit/SKILL.md](subskills/ui-maturity-audit/SKILL.md) |

The modes feed each other. A pursuit run that follows a released pursuit epic should start
with a scoped QC pass over the surfaces the fleet shipped (did the moves actually land as
designed?) — its `live-confirmed / source-confirmed / inferred` verdicts become part of the
Phase 0 baseline, and tier-board *movement* is only claimed for surfaces QC confirmed. In the
other direction, the QC subskill's [failure taxonomy](subskills/ui-maturity-audit/references/failure-taxonomy.md)
is a standing input to the Phase 1 auditor prompts (the "systemic sins" list), and NEW failure
shapes a pursuit run discovers get appended there (its maintenance contract).

## Phase 0 — Ground and scope (inline, before any fan-out)

1. **Load doctrine** (these define "world-class" for this repo — every subagent prompt must
   cite them):
   - `about/heart-and-soul/vision.md` — what the system is for; the success criterion.
   - `about/heart-and-soul/design-language.md` and the normative spec
     `openspec/specs/dashboard-design-language/spec.md` (Dispatch language; `frontend/src/index.css`
     is normative for values).
   - `docs/frontend/purpose-and-single-pane.md` — the single-pane / detect→diagnose→act doctrine.
   - Butler manifestos (`roster/*/MANIFESTO.md`) for the ecosystem lenses.
2. **Build the page inventory** from `frontend/src/router-config.tsx` — the route table is the
   source of truth, NOT a directory listing of `frontend/src/pages/`. Group routes into page
   *surfaces* (e.g. entity index + detail = one surface) to keep the fan-out ~20–25 agents.
3. **Build the "already known" ledger** — the dedup input injected into EVERY subagent prompt:
   - Prior pursuit dossiers: `ls docs/redesigns/*jarvis*` and read their move lists + tier boards.
   - Open work: `bd list --json` filtered to open/in-progress design and feature beads; note
     epics still executing (e.g. children of prior pursuit epics).
   - Recently merged redesign PRs on the audited surfaces (`git log --oneline -50`).
   Summarize into a compact bullet list: "known and in-flight — do not re-report".
4. **Snapshot the tier-board baseline** from the most recent prior dossier so the new run can
   report movement (weak→functional, functional→solid, …).

## Phase 1 — UX pursuit fan-out (Workflow)

One subagent per page surface plus four cross-cutting sweeps (shell/discoverability, visual
language, interaction speed, accessibility). Prompt templates and the structured-output JSON
schema live in [references/dispatch-prompts.md](references/dispatch-prompts.md). Non-negotiables
baked into every prompt:

- Hold the surface to the **/th-design bar** (each agent loads
  `/home/tze/.claude/skills/th-design/SKILL.md` and the one subskill relevant to its lens).
- Judge against the **north star** (quote it from the prior dossier's "North star" section, or
  re-derive from vision.md) — earned calm, no fabricated data, unbroken drill-down spine,
  keyboard-first, one visual language.
- **Backward compatibility is waived.** Propose the ideal design; redesigns and removals are
  in scope.
- Every finding cites evidence as `file:line`. Every surface gets a verdict tier:
  `world-class | solid | functional | weak | broken`, a "JARVIS gap" paragraph, and a ranked
  move list.
- The "already known" ledger is included verbatim; findings that duplicate it must be dropped
  by the agent, not the synthesizer.

## Phase 2 — Ecosystem pursuit fan-out (Workflow, runs concurrently with Phase 1)

One ideation agent per lens. Default lenses (add/remove per run as the owner directs):

| Lens | Ground in | Pursuit question |
|---|---|---|
| New connectors | `adding-connectors-and-modules` skill, existing account registry, roster manifestos | Which external services would most expand what butlers can perceive/do for the owner? |
| Inference flow | `src/butlers/core/model_routing.py`, `public.model_catalog`, spawner + session lifecycle | Where does the trigger→spawn→act loop waste latency, money, or capability? Smarter tiering, caching, session reuse, streaming? |
| Knowledge graph | `docs/modules/memory.md`, memory module, `relationship.entity_facts` vs `relationship.facts` split | How does the graph grow scalably and stay trustworthy — promotion, decay, provenance, cross-butler reads? |
| Cross-butler interaction | Switchboard, MCP-only doctrine (`about/heart-and-soul/architecture.md`) | What would butlers accomplish by genuinely collaborating that none can alone? |
| Proactivity & triggers | Scheduler, calendar events, connector ingestion, notification flow | Where should the system act or surface things before being asked — without becoming noisy? |

Each lens agent must return **concrete, integration-point-named proposals** (which module, which
schema, which spec would change), scored for owner-value vs. build-cost, deduped against the
known ledger, and manifesto-aligned (a proposal that fits no butler's manifesto must say which
new butler or manifesto amendment it implies).

## Phase 3 — Synthesis (single agent, barrier after Phases 1–2)

- Dedupe across agents and against the known ledger once more.
- Produce: tier board (with movement vs. baseline), systemic themes (cross-page defects with
  exemplars), and a single ranked move list (~10–15 moves) mixing UX moves and ecosystem
  extensions. Each move: what, why (doctrine citation), evidence, rough slice plan.

## Phase 4 — Deliverables

1. **Durable dossier** — commit to main (docs-only, safe for direct commit):
   `docs/redesigns/YYYY-MM-DD-jarvis-pursuit.md` (north star, tier board + movement, themes,
   ranked moves) and `-data.json` (full per-agent structured output; document the
   `jq '.audits[] | select(.page=="<key>")'` access pattern in the md).
2. **Artifact report** for the owner (load `artifact-design` skill first) — the readable
   version of the dossier.
3. **Gated beads epic** — see protocol below.
4. **Memory** — write/update a `reference` memory with the artifact URL, dossier path, epic id,
   and gate id, linking `[[reference-jarvis-frontend-audit-2026-07]]` and successors.

## Bead-filing protocol (CRITICAL — fleet-trigger hazard)

Creating READY beads in this repo auto-starts the autonomous fleet within minutes. A pursuit
run is *planning*, not execution. Always:

1. Create a `[HOLD]` gate bead first, **assigned to the owner**.
2. Create the epic + one child per move; make every child depend on the gate so all are
   blocked. bd rejects a task blocking an epic ("epics can only block other epics") — so also
   **assign the epic itself to the owner** to keep it off `bd ready`.
3. Every bead description cites its evidence and points at the dossier JSON.
4. Bulk creation: `bd create` Dolt-commits per write (~7s, serialized) — use
   `--dolt-auto-commit batch` and one `bd dolt commit` at the end. **Never** use
   `bd create --graph` (its `--dry-run` actually creates beads and drops deps); add edges with
   `bd dep add`.
5. Release is the owner's move: closing the gate bead un-blocks the children and the fleet
   executes. Say this explicitly in the final report.

## Scaling and cadence

- Default scale: ~20–25 page-surface agents + 4 cross-cutting + ~5 lens agents + 1 synthesis.
  Honor any `+Nk` token budget the owner gives (Workflow `budget`).
- On a re-run soon after a prior release, expect fewer NEW findings — that is success, not
  failure. Report tier movement prominently; do not pad the move list to hit a count.
- If the fleet is mid-execution on a prior pursuit epic, prefer auditing surfaces it has
  already landed (to measure) and lenses it is not touching (to ideate); note the overlap in
  the report instead of filing colliding beads.
- **Stagger the fan-out over 12–16 hours (MANDATORY default).** A full-concurrency run blows
  through the owner's 5-hour usage window. Structure the Workflow script for staggered
  execution: order all fan-out `agent()` thunks deterministically, split them into hourly
  batches of 2–3 via `BATCH_SIZES` and an `args.batch` cumulative counter (run batches
  `0..args.batch-1` sequentially, each `await parallel(slice)`; return a partial result if
  batches remain, run synthesis only after the final batch). Launch batch 1, then re-invoke
  with `{scriptPath, resumeFromRunId: <previous run id>, args: {batch: N+1}}` on each hourly
  `ScheduleWakeup(3600)` tick — prior batches hit the resume cache so only the newest batch
  runs live. Keep a state file in the scratchpad (last batch, last run id) so wakeups survive
  context summarization. Workflow-completion notifications are informational only — never
  launch the next batch early on one; the wakeup drives the cadence. Only run all batches
  at once if the owner explicitly says the usage limit is not a concern.
- **Persist every batch's results to disk immediately (quota-disruption safety).** Never let
  harvested findings live only in conversation context or an unfinished Workflow run: a
  usage-limit cutoff mid-run must lose at most one batch. After each batch's completion
  notification, harvest its agents' structured outputs from the run's transcript dir
  (`journal.jsonl` records each `agent()` return value; `agent-<id>.jsonl` files are the
  fallback) and append them to a durable harvest file (e.g.
  `docs/redesigns/<date>-jarvis-pursuit-harvest.json` or the scratchpad state dir), keyed by
  agent label. The state file (last batch, last run id, harvest path) plus the harvest file
  must together be sufficient to resume or hand-synthesize the run from a cold start — the
  final synthesis/dossier can then be rebuilt from disk even if the resume chain or session
  context is lost.
- **Precedent:** run 06 (2026-07-22) executed 26 agents as `[3,2,2,2,2,2,2,2,2,2,2,2]`
  hourly batches after an all-at-once launch had to be killed mid-flight to protect the
  owner's limit.
