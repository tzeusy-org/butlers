## 1. Owner sign-off (gate)

- [ ] 1.1 Confirm the Intent/Evidence/Activity reframe and that only the
  `activity` layer is counted (the "calendar = 5h" fix).
- [ ] 1.2 Confirm the narrow doctrine amendment: chronicler MAY synthesize
  insights into its own schema (memory module) and propose entity facts to
  `relationship` over MCP; still no external ingest, no owner notifications.
- [ ] 1.3 Confirm the Activity lane taxonomy (`sleep, exercise, work, play,
  social, travel, eat, rest`) and that music/gaming/calendar are no longer
  top-level lanes.

## 2. Terminology cleanup (independent, no deps)

- [ ] 2.1 Replace "memory butler" → "memory module" in
  `openspec/specs/relationship-facts/spec.md` (line ~57),
  `entity-identity/spec.md` (lines ~33, ~421), `module-calendar/spec.md`
  (line ~152), `dashboard-permissions/spec.md` (line ~75).
- [ ] 2.2 Fix the same phrasing in `roster/butlers` CLAUDE.md / docs that refer
  to a "memory butler" entity graph.

## 3. Layer + confidence storage (depends on §1)

- [ ] 3.1 Migration: add `layer` (`intent|evidence|activity`), `confidence`
  (`high|medium|low`), and an `evidence_refs` surface to chronicler episodes
  (column or derived view over `episode_event_links`).
- [ ] 3.2 Backfill/classify existing episodes into layers (calendar → intent,
  inferred → activity, raw point projections → evidence).
- [ ] 3.3 Tests: layer classification; overlapping episodes still permitted.

## 4. Counting fix in aggregations (depends on §3)

- [ ] 4.1 `src/butlers/chronicler/aggregations.py`: count `activity` layer only;
  drop the "calendar" category; calendar time attributed only via a corroborating
  activity lane.
- [ ] 4.2 Map sources into Activity lanes (music/gaming → play, sessions → work,
  etc.).
- [ ] 4.3 Tests: uncorroborated 5h calendar block → 0s in every lane (regression
  for the reported defect); corroborated block counts under its activity lane;
  music+gaming roll into `play`.

## 5. Confidence derivation + evidence chain (depends on §3)

- [ ] 5.1 Derive `confidence` from count of independent corroborating evidence
  kinds in the deterministic adapters.
- [ ] 5.2 Populate `evidence_refs[]` from `episode_event_links`.
- [ ] 5.3 Tests: 3 independent kinds → high; single weak signal → low (still
  counted).

## 6. Deterministic candidate projectors (depends on §3, §5)

- [ ] 6.1 Add candidate-activity emission to existing adapters (health→exercise/
  sleep, sessions→work, spotify/steam→play, owntracks→travel).
- [ ] 6.2 New `src/butlers/chronicler/adapters/comms.py`: project message bursts
  (gmail/telegram/whatsapp/discord) → `social`, resolving participants via
  `relationship.entity_facts`.
- [ ] 6.3 `butler.toml`: add comms projection jobs on cadence.
- [ ] 6.4 Tests: each rule emits candidates without LLM; comms burst → social
  activity; unresolved participant degrades to unattributed + lower confidence.

## 7. Day-close reconciliation upgrade (depends on §6)

- [ ] 7.1 Upgrade the `chronicler_day_close` prompt + bundle from "summarize" to
  "reconcile": merge duplicate candidates, resolve intent-vs-evidence conflicts,
  label ambiguous blocks, write narrative. Preserve no-per-event-LLM (§4.8).
- [ ] 7.2 Tests: conflicting intent dropped against evidence; duplicate
  candidates merged with combined evidence; token bound respected.

## 8. Memory write-back loop (depends on §1.2 doctrine + §7)

- [ ] 8.1 Doctrine: amend `roster/chronicler/MANIFESTO.md` + `about/heart-and-soul/v1.md`
  for the narrow write-back.
- [ ] 8.2 Enable the memory module for the chronicler (`butler.toml` + module
  migrations into `chronicler.*`).
- [ ] 8.3 Day-close writes synthesized insights (sleep debt, social cadence,
  lane skew) into own-schema memory with provenance + decay.
- [ ] 8.4 Self-reminders: mark low-confidence blocks for re-reconciliation.
- [ ] 8.5 Entity-enrichment: propose recurring-companion facts to `relationship`
  over MCP (never direct write).
- [ ] 8.6 Tests: insight lands in own schema only; enrichment is an MCP proposal;
  owner not notified.

## 9. API read surfaces (depends on §4, §5, §8)

- [ ] 9.1 Reshape `aggregate/by-category` (activity-only, lane taxonomy, per-lane
  low-confidence breakdown).
- [ ] 9.2 New endpoints: daily balance (vs-usual), trends (week/month), who-you-
  were-with, activity evidence chain, low-confidence correction prompts.
- [ ] 9.3 Models in `roster/chronicler/api/models.py`; tests for each endpoint.

## 10. Frontend page surface (depends on §9)

- [ ] 10.1 Remove `AggregatePieChart`.
- [ ] 10.2 Day Ribbon timeline + ghost intent track + click-to-evidence-chain.
- [ ] 10.3 Balance rings vs-usual.
- [ ] 10.4 Who-you-were-with panel; where-you-went map trail (existing privacy
  contract).
- [ ] 10.5 Low-confidence correction prompts wired to corrections overlay.
- [ ] 10.6 Week/month zoom-out trends lens.
- [ ] 10.7 FE tests (vitest) + eslint gate; full `npm run build`.

## 11. Validation

- [ ] 11.1 `openspec validate chronicler-intent-evidence-activity`.
- [ ] 11.2 Full local quality gates (ruff, pytest incl. integration, FE build).
- [ ] 11.3 Manual: load `/chronicles` for the reported Saturday; confirm no
  "calendar = 5h", lanes legible, evidence chains resolve.
