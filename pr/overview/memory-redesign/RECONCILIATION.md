# Reconciliation — memory redesign

> Post-ship audit of what landed on `main` vs. this pack, per README "Next
> steps" §4 and brief §6. Audited 2026-06-13 by three independent code-level
> passes (registers/overture/rail, detail pages/housekeeping, backend
> contracts + FE→BE wiring) against the recipes' acceptance checklists.
> Implementation: beads `bu-awo8k.1–.8` (backend), `bu-2ix8d.1–.9` (frontend),
> `bu-xgv9q` (spec), PRs #2166–#2206. Closeout: `bu-g2mwk`.

## Verdict

**Landed faithfully.** All eight backend contracts implemented with tests and
live FE consumers; all seven frontend surfaces match their recipes' acceptance
checklists, including color discipline (zero red/amber/green on a healthy
page), belief typography (confidence as numeral, fading as dimming), exact
Voice templates and serif-italic empty states, the action-less "write-up
overdue" rail row, and the one-search rule. The spec amendment landed as
OpenSpec change `redesign-memory-house-ledger` and is now synced into the main
specs and archived (this PR).

## Drift found and fixed (this closeout PR)

1. **Tier-card grid survived on the page.** `MemoryPage` still rendered the
   old `MemoryTierCards` (shadcn Card + skeleton-pulse + percentage stats)
   between the overture and the registers, violating the amended spec's
   "MUST NOT render any tier-card grid". Root cause: the OpenSpec change was
   never applied to the main specs, so a reconciliation pass (`bu-d9im5`) read
   the *old* spec's tier-card MUSTs as binding. Fixed: render removed,
   `MemoryTierCards.tsx` and the orphaned `badges.tsx` deleted, change synced
   + archived so the main spec now states the house-ledger grammar.
2. **Dead wire: `superseded_by`.** Backend bu-awo8k.8 landed and returns
   `superseded_by` on `GET /facts/:id`, but the frontend `Fact` type lacked
   the field and `FactDetailPage` read `superseded_by_id` (wrong name) behind
   a stale "gated off" comment — the reverse supersession link could never
   render. Fixed: field added to the type, page reads `fact.superseded_by`,
   gating test inverted to assert the link renders when present.

## Deliberate deviations from the pack (sanctioned)

- **`MemoryBrowser` name retained** as the registers host (rewritten in
  place, optional `butlerScope` kept; `ButlerMemoryTab` decoupled). Sanctioned
  by `bu-d9im5` spec amendment (#2205); the pack's "delete the browser chrome"
  intent is satisfied — the chrome is gone, the name stayed.
- **Inspect returns register-shaped rows** (`bu-by2n0` #2199, consumed by
  `bu-gtnel` #2202) — discovered improvement beyond the pack so search results
  are byte-identical to browse rows without client-side re-fetch.
- **Consolidation cadence constant**: "write-up overdue" uses
  `CONSOLIDATION_CADENCE_HOURS = 24` client-side (2× ⇒ overdue at 48h is
  generous vs. the 6h cron; conservative, alarm-averse — within Dispatch
  composure rules).

## Known gaps (follow-up beads filed)

| Gap | Severity | Bead |
|---|---|---|
| Register cross-fade on pill switch (MEMORY_LANGUAGE §8) not implemented — registers swap instantly | cosmetic | bu-9qekw |
| Test hardening: stats multi-pool aggregation (MAX/SUM across butler schemas), consolidation write-on-completion e2e, back-button URL-state navigation | medium | bu-ezg4r |
| Retention PUT lacks the client-side kind whitelist guard (`isValidRetentionKind` exists, never called) | low | bu-itute |

(Filed: bu-9qekw, bu-ezg4r, bu-itute.)

## Spec-debt notes (not regressions)

- `dashboard-domain-pages` retains the pre-existing "Memory hooks" table
  requirement alongside the new "Memory hooks (house-ledger)" requirement;
  they are compatible (same hooks, same refresh intervals) but a future delta
  should merge them.
- Repo-wide `openspec validate --specs` has widespread pre-existing strictness
  failures; this sync *reduced* dashboard-domain-pages scenario errors from 12
  to 8 and introduced none.

## FE→BE wiring verification (every new affordance)

| Affordance | Endpoint | Wired? |
|---|---|---|
| Voice / KPI / pipeline / rail stats | `GET /stats` + 3 new fields | ✓ consumed by `MemoryOverture`, `AttentionRail` |
| Daybook status pills | `GET /episodes?status=` | ✓ `EpisodeParams.status` → pills → API |
| Rail fading-important count | `GET /facts?validity=fading&importance_min=8` | ✓ `meta.total` consumed |
| Episode derived facts | `GET /facts?source_episode_id=` | ✓ `useFactsByEpisode` |
| Confirm / Retract | `POST /facts/:id/confirm` / `retract` | ✓ pills wired, mutations invalidate caches |
| Superseded-by reverse link | `GET /facts/:id` `superseded_by` | ✓ after this closeout (was a dead wire) |
| Stale embeddings (rail + housekeeping) | `GET /reembed/pending` | ✓ per-tier counts |
| Unified search | `GET /inspect` (register-shaped rows) | ✓ adapters prefer embedded rows |
