## 1. Place stays bridge sparse reporting (bu-r3fmja.1)

- [x] 1.1 `cluster_points` bridges same-place and departure gaps up to the 16 h cap, recording `bridged`; departure-bridged stays use minute precision.
- [x] 1.2 `place_episode` is projected with `layer = evidence`.
- [x] 1.3 Migration tombstones existing `owntracks.place_cluster` `place_episode` rows and resets that adapter's watermark and carryover, so retained fixes are re-clustered.

## 2. Anchor places (bu-r3fmja.1)

- [x] 2.1 `butlers.chronicler.anchors`: pure resolver (registry > graph > env > inferred overnight home) plus a DB loader reading `public.owner_places`, the owner's graph facts and `relationship.address_geocodes`, degrading to "no anchor" when a surface is absent.
- [ ] 2.2 Grant `butler_chronicler_rw` SELECT on `relationship.address_geocodes` (lands with task 5.1).

## 3. Situational activity precedence (bu-r3fmja.1)

- [x] 3.1 Pure `butlers.chronicler.situation`: where-segments, tail hold, anchor classification, precedence subtraction, 15-minute floor.
- [x] 3.2 `chronicler.situation_inferred` adapter with per-day re-derivation that tombstones stale blocks and keeps corrected ones; job, schedule, source contract, staleness cadence.
- [x] 3.3 Category/lane mappings for the new episode types in the backend and in the frontend fallback map.

## 4. Gap interview asks about one block (bu-r3fmja.2)

- [x] 4.1 `evaluate_gap_interview` selects the longest uncovered waking block and its location context; the question no longer asks about a work day unless an occupation block anchors it.
- [x] 4.2 Lane answers (`work`, `social`, `play`, `eat`, `rest`) write one owner-attested `chronicler.owner_answer` activity episode, idempotently; the telegram keyboard and connector toasts carry the new answers.

## 5. Relationship geocodes owner anchor addresses (bu-r3fmja.3)

- [ ] 5.1 Migration: `relationship.address_geocodes` and its Chronicler SELECT grant.
- [ ] 5.2 OneMap/Nominatim geocoder client, with optional `ONEMAP_EMAIL`/`ONEMAP_PASSWORD`, rate limits and backoff.
- [ ] 5.3 Deterministic job `relationship_geocode_owner_addresses` that selects only owner anchor addresses, re-geocodes on text change and backs off failures.

## 6. Docs

- [ ] 6.1 Chronicler docs (`docs/butlers/chronicler.md`, roster AGENTS notes) and Relationship docs describe anchors, precedence, geocoding and the owner's registration path.
