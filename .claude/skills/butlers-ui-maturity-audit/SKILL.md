---
name: butlers-ui-maturity-audit
description: >
  QC sweep of the Butlers dashboard for UI maturity and feature completeness, organised around
  end-to-end USER FLOWS: take a goal a user is trying to accomplish (grounded in the project's
  manifesto/direction), walk the happy-path step by step, and judge whether the experience is
  pleasant, mature, honest, and backed end to end. Catches dead buttons, controls that persist
  but change nothing, fake/placeholder data, misleading UI, orphaned routes, and spec-required
  behaviour never built. Fans out one agent per flow; each traces handler -> API client ->
  backend route -> data consumer, and drives the flow live when the dev stack is up. Triggers:
  "QC the dashboard", "is this flow actually wired", "audit UI maturity / feature completeness",
  "is the X page real or just a skin", "did the redesign actually ship the behaviour", "is this
  showing fake data", "walk the connect-account flow end to end". Not for pure visual/UX critique
  (use impeccable) or spec-vs-code drift bead-filing (use reconcile-spec-to-project).
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-06-14"
compatibility: >
  Static audit needs only repo-root access. Live-stack verification (optional but preferred)
  needs the Docker Compose dev stack up; route to /butler-dev-debug for its primitives.
---

# Butlers UI Maturity Audit (QC)

A **dev-time** quality-control sweep of the Butlers dashboard. It is the QC counterpart to the
QA *butler* (`butler-qa-invoke`): QA is a runtime staffer that investigates the owner's life;
this skill is an engineering audit you run against the dashboard's own surfaces, on demand,
before or after a redesign merges.

The thing it catches is **the gap between a UI that looks finished and one that works**. A
redesign can ship a beautiful surface where every control fires a real network call and still
leave the user stranded: the call persists a row nothing reads, the field the UI renders is never
written, the button has no backend, the "Live" badge is a `setTimeout`, or the spec-required
action was never wired. None of the adjacent tools cover that gap:

- `impeccable` — visual/UX/aesthetic critique; never traces to the backend.
- `reconcile-spec-to-project` — spec-vs-code drift, files beads; spec-centric, not flow-centric.
- `craft-and-care` / `th-engineering` — engineering quality of a *change*, not a flow sweep.

## Use When

- After a redesign lands, or before merging a page-level change — does the flow work end to end?
- Auditing a surface for dead buttons, decorative controls, fake data, or misleading UI.
- Asking "is this page real, or just a skin over stubs?"

## Do Not Use When

- You want visual/aesthetic/UX-copy critique with no backend question → `impeccable`.
- You want a spec-vs-implementation reconciliation that files beads → `reconcile-spec-to-project`.
- You're debugging one concrete failed session/request in the dev stack → `/butler-dev-debug`.

## The unit of analysis is the USER FLOW, not the page

Enumerating every control on a page is the *weak* form of this audit — it finds dead `onClick`s
(the rarest, least-deceptive failure) and misses the flows that span pages. The **strong** form
picks a thing a real user is trying to *accomplish* and walks it end to end. For every flow and
every step in it, answer these five questions — they are the rubric:

1. **What is the pleasant experience here?** Define the happy-path for *this* user goal, grounded
   in the project's manifesto/direction (not invented). What would "this just works" feel like?
2. **What went wrong?** Where does the real implementation diverge from that happy-path?
3. **Is every UX element along the way mature?** Loading/empty/error/degraded states, feedback on
   actions, sensible defaults, recovery from the trap cases — at *each* step, not just the start.
4. **Is anything misleading or poorly designed?** A toast that claims success for work that didn't
   happen, a status that's hardcoded, a control that looks editable but is read-only, a "Live"
   badge that's fake. Misleading beats missing for user-trust damage.
5. **Does the backend comprehensively support the happy-path?** Not "does an endpoint exist" —
   does the *whole* path have real, consumed, non-stub backing, including the unhappy branches the
   user will hit (revoked token, empty result, partial failure)?

The Secrets audit's journey-based pass (connect → view scopes → widen → expiry → revoke → recover)
is the exemplar: it surfaced far more than a control-by-control sweep of the same page.

## Hard Rules

1. **Investigate only. Never edit, never run quality gates or migrations.** Output is a report
   (and beads only on request). This binds the orchestrator **and** every fan-out agent — the
   prompt template states it; do not relax it.
2. **"Endpoint exists" ≠ "feature works." "Persists" ≠ "consumed."** Trace every step: React
   handler → API client fn → backend route → **does runtime code read what the route wrote?** A
   `PUT` that 200s into a table nothing queries is *decorative* — more dangerous than a dead
   button, because the user believes it took effect. Prove the reader with `grep`.
3. **Judge from the user's seat** using the five questions above. A defect is anything that makes
   the flow unpleasant, dishonest, or incomplete — not just a crash.
4. **Prefer live verification over static inference.** When the dev stack is up, *drive the flow*
   and follow the request through logs and DB rather than only reading code — see
   [references/runtime-verification.md](references/runtime-verification.md). Static-only findings
   are "suspected"; live-confirmed findings are "confirmed."
5. **Cite `file:line` for every claim, and verify against current `main`.** Active branches move
   fast; re-read the live file before calling a control dead. Separate confirmed from suspected.
6. **One subagent per flow** (clean context each; orchestrator stays small and only synthesises).

## Workflow

### Phase 0 — Derive the flows (orchestrator, inline)

Ground the flow list in **project shape**, do not invent goals. Read the manifesto/doctrine for
what the surface is *for* (`heart-and-soul`, `lay-and-land`), and the intended end-state for what
was *promised* (redesign briefs + openspec). Then enumerate candidate surfaces deterministically:

```bash
uv run .claude/skills/butlers-ui-maturity-audit/scripts/scope_surfaces.py
```

It emits the route→file map, redesign briefs, openspec change dirs, modified `dashboard-*` specs,
and feature-flag gating. **You** cluster those surfaces into user flows (judgment — the set
drifts). See [references/user-flows.md](references/user-flows.md) for how to derive flows from
project shape, the experiential rubric in full, and the living flow catalog.

### Phase 1 — Walk each flow end to end (fan out, parallel)

One `general-purpose` agent per flow, all dispatched in one message. Pass each agent
[references/audit-agent-prompt.md](references/audit-agent-prompt.md) verbatim (fill the
placeholders), append the trace-layer + topology pointers from
[references/file-location-map.md](references/file-location-map.md), and — if verifying live —
[references/runtime-verification.md](references/runtime-verification.md). Agents hunt the shapes
in [references/failure-taxonomy.md](references/failure-taxonomy.md).

### Phase 2 — Synthesise (orchestrator)

Per flow: where the experience breaks, which steps are immature/misleading, whether the backend
covers the happy-path *and its unhappy branches*. Cross-cut by taxonomy shape. Rank the global
findings by **user-trust damage** (a control that lies > one that's visibly missing > a cosmetic
gap). **Write-back:** if any agent reported a failure shape that fits no taxonomy entry, append it
to `references/failure-taxonomy.md` in this run (its maintenance contract). Offer to file beads
(this repo tracks work in `bd`) `discovered-from` the audit if asked.

## References (load progressively)

- [references/user-flows.md](references/user-flows.md) — Phase 0: derive flows from project shape; full experiential rubric; living flow catalog.
- [references/failure-taxonomy.md](references/failure-taxonomy.md) — Phase 1/2: the ranked failure shapes to hunt; living catalog (append new shapes you find).
- [references/runtime-verification.md](references/runtime-verification.md) — when driving a flow through the live dev stack and tracing the request (builds on `/butler-dev-debug`).
- [references/file-location-map.md](references/file-location-map.md) — the FE→BE trace layers; routes to `about/lay-and-land/` for authoritative topology.
- [references/audit-agent-prompt.md](references/audit-agent-prompt.md) — the verbatim flow-QC prompt to hand each fan-out agent.
- [scripts/scope_surfaces.py](scripts/scope_surfaces.py) — Phase 0 deterministic surface inventory (route→file, briefs, changes, flags).
