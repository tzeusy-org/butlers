# Tasks — redesign-memory-house-ledger

This change amends specs and enumerates the backend wires; implementation is
carried by two beads epics (backend contracts → house-ledger frontend) tracked
in `bd` under the `memory-redesign` label. Tasks below are grouped to mirror
those epics. Frontend tasks are blocked on the backend wires they consume.

## Track A — Backend contracts (epic `memory redesign — backend contracts`)

Dependency order: bead 1 → bead 2; beads 3–8 parallel.

- [ ] A1 — Alembic migration: create additive `public.consolidation_runs`
  (`id, butler, consolidated_at, episodes_processed, facts_produced,
  facts_updated, rules_created, confirmations_made, errors`); no change to
  existing memory tables. Wire write-on-completion in `consolidation.py`. (brief §3 bead 1)
- [ ] A2 — Extend `GET /api/memory/stats` with `last_consolidation_at`,
  `last_consolidation_facts_produced`, `dead_letter_episodes` (additive). (brief §3 bead 2; blocked by A1)
- [ ] A3 — Add `POST /api/memory/facts/:id/confirm` delegating to
  `storage.confirm_memory()`. (brief §3 bead 3)
- [ ] A4 — Add `POST /api/memory/facts/:id/retract` (`validity='retracted'`)
  delegating to `storage.forget_memory()`. (brief §3 bead 4)
- [ ] A5 — Extend `GET /api/memory/episodes` with `status` enum filter
  (legacy `consolidated` bool preserved; `status` takes precedence). (brief §3 bead 5)
- [ ] A6 — Extend `GET /api/memory/facts` with `source_episode_id` filter. (brief §3 bead 6)
- [ ] A7 — Extend `GET /api/memory/facts` with `importance_min` filter. (brief §3 bead 7)
- [ ] A8 — Extend `GET /api/memory/facts/:id` with `superseded_by` reverse
  lookup (`WHERE supersedes_id = $1`). (brief §3 bead 8)
- [ ] A9 — Tests for each delta (envelope conformance, filter precedence,
  reverse lookup, mutation outcomes, additive-table write-on-completion).

## Track B — House-ledger frontend (epic `memory redesign — house-ledger frontend`)

One bead per bundle recipe (`pr/overview/memory-redesign/prompts/00–07`).
Blocked on the Track A wires each consumes (stats extension blocks overture +
rail; mutations gate the fact commit footer non-blockingly).

- [ ] B0 — Foundation: URL state (`register/q/kind/validity/status/offset`,
  defaults unwritten so deep-links round-trip), `use-memory.ts` hooks, pure
  derived fns (`effectiveConfidence` with per-day decay over fractional days,
  clamped [0,1]; `permanenceTag`; `consolidationGlyph`), `useSearchParams`
  pattern from `IngestionPage.tsx`, and audit/update of hardcoded
  `/memory?tab=` links. (recipe 00)
- [ ] B1 — Overture band (eyebrow / display / Voice / 4-cell KPI strip). (recipe 01)
- [ ] B2 — Pipeline band (single mono line, `─→` connectors, red dead-letter
  numeral only when > 0). (recipe 01)
- [ ] B3 — Ledger register (Facts), with belief column + fading dim + `↳`
  glyph + validity pills. (recipe 02)
- [ ] B4 — Standing-orders register (Rules): `§NN` gutter, tally line, maturity
  mono word, anti-pattern red sliver. (recipe 03)
- [ ] B5 — Daybook register (Episodes): day-grouped feed, time gutter,
  ButlerMark, consolidation glyph, expandable rows, status filter. (recipe 04)
- [ ] B6 — Unified search (one input, `/` focus, Enter submit, kind pills,
  results in register shapes) + offset pagination, page size 50. (recipe 05)
- [ ] B7 — Attention rail (5 condition rows; "write-up overdue" **action-less**)
  + Recent activity quiet list, de-carded. (recipe 05)
- [ ] B8 — Detail pages: editorial skeleton, decay-arithmetic line, glyphs/
  numerals replacing bars/badges, provenance section (omit-when-empty),
  Confirm/Retract commit footer gated on backend. (recipe 06)
- [ ] B9 — Housekeeping band: retention policies (kept), compaction log (kept),
  embeddings surface; demoted to quiet bottom band. (recipe 07)
- [ ] B10 — Preserve legacy `MemoryBrowser` + `butlerScope` for
  `ButlerMemoryTab`; new `/memory` page MUST NOT depend on it. (Open question 12)
- [ ] B11 — Test churn: rewrite `MemoryBrowser`/`/memory` tests; adapt detail-
  page tests; retention/compaction/reembed tests survive. (brief §2)
- [ ] B12 — Frontend gates: `eslint .`, `tsc`, `vitest` all green (CI gates).

## Track C — Reconciliation

- [ ] C1 — After implementation, write
  `pr/overview/memory-redesign/RECONCILIATION.md` auditing what landed vs. the
  pack, including FE→BE wiring verification for every new affordance (no dead
  buttons; Confirm/Retract gated on endpoints).
