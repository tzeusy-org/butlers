# Design — redesign-memory-house-ledger

## Context

The `/memory` page is the surface where the single owner audits what the house
believes. The current spec codifies a card-grid + unified-table implementation
that heart-and-soul doctrine (`about/heart-and-soul/design-language.md:44-62`)
names a regression. This change formalises the house-ledger grammar
(`pr/overview/memory-redesign/MEMORY_LANGUAGE.md`) as the spec, with binding
intent from `pr/overview/memory-redesign/VISION.md` and the integration brief
(`docs/redesigns/2026-06-12-memory-brief.md`). Below are the decisions the brief
flagged for the spec phase to pin (brief §5 Open questions 7–12, §4 guardrails).

## Decisions

### Decision: `inspect` pagination is one offset across the union (v1)
The unified search is backed by `GET /api/memory/inspect`, whose current handler
paginates the union of kinds with a single offset. Per brief Open question 7 we
**pin v1 semantics to one offset across all kinds**, not per-kind offsets. The
frontend reads and writes one `offset` URL param. Per-kind pagination is a
possible future refinement, out of scope here.

### Decision: page size 50, offset-based
The retired browser used page size 20 with per-tab search. The house-ledger
registers use page size **50** with one shared search and offset pagination
(`1–50 of N` footer + prev/next pills). 50 suits the denser hairline-row rhythm
and reduces paging friction for the owner scanning a ledger.

### Decision: the rail "write-up overdue" row is action-less — permanently
Consolidation is a pre-existing 6-hourly scheduled cron per butler. The brief's
Phase D cost analysis (§4) grades every redesign affordance green **on the
condition** that no "run consolidation now" affordance is ever added — that is
the only place a future change could multiply the pre-existing spawn cost. We
encode the action-less rail row as a binding spec MUST-NOT so future beads
proposing a run-now button are rejected against the spec, not just the brief.

### Decision: `consolidation_runs` is additive-only
VISION's no-migration intent forbids storage/schema migration of memory tables.
`last_consolidation_facts_produced` is underivable from existing tables, so we
add **one new audit table** `public.consolidation_runs`, written once per
successful run from counts the pipeline already returns. No existing memory
table is altered. The spec states the additive-only constraint explicitly so the
table cannot grow into a covert memory-data migration.

### Decision: `MemoryBrowser` rewritten in place as the registers host; `ButlerMemoryTab` decoupled
`ButlerMemoryTab` on butler detail pages is out of scope for this redesign
(brief §1, Open question 12). Rather than maintain two browser components, we
rewrite `MemoryBrowser` in place into the `/memory` Band-3 registers host
(search + register pills + focused register / results) and keep an optional
`butlerScope` prop for a future butler-scoped mount. `ButlerMemoryTab` is
reworked to be self-contained — it no longer imports `MemoryBrowser` or any
`components/memory/*` module and draws from its own per-butler hooks — so
restyling `MemoryBrowser` cannot silently break the tab. A future change may
mount the house-ledger registers (via `MemoryBrowser` with `butlerScope`) on
`ButlerMemoryTab`.

### Decision: confidence is effective (decayed), rendered as ink
The fading threshold and the ledger belief numeral both use **effective**
(decayed) confidence, computed from raw `confidence`, per-day `decay_rate`, and
days since `last_confirmed_at` (fractional days, clamped to [0,1]). Decay is
expressed by dimming the row foreground to `--dim`, never by color or
strikethrough. The detail page states the arithmetic in one honest mono line.

### Decision: no dead buttons — Confirm/Retract gated on backend endpoints
Per prior FE→BE reconciliation experience, every affordance must have a verified
wire. The fact detail Confirm/Retract pills render only when the backend
`POST /api/memory/facts/:id/{confirm,retract}` endpoints are present; an absent
endpoint means an absent affordance, never a non-functional button.

## Risks / trade-offs

- **Hard UI cut.** The card-grid `/memory` and the unified table are removed;
  `MemoryBrowser` is rewritten in place into the `/memory` registers host and
  `ButlerMemoryTab` is decoupled from it. Test churn is real (MemoryBrowser/
  `/memory` tests rewrite) but bounded.
- **One-offset search.** Mixed-kind search results paginate as a union, which
  can interleave kinds across page boundaries. Accepted for v1 (brief Q7).
- **Additive table latency.** `/stats` now aggregates `consolidation_runs`
  across butler pools; this follows the existing fan-out pattern and adds one
  indexed read per pool.

## Open questions carried to implementation

- Exact KPI strip vs. KV-band component boundary (brief Q8) — component-level,
  resolved in the frontend epic, not spec-level.
- Housekeeping one-commit-per-surface reading (brief Q6) — if review reads the
  band as one surface, demote `re-embed` to secondary; UI-level.
