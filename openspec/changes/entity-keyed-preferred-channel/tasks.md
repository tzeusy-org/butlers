# Tasks — entity-keyed-preferred-channel

Backend (groups 1–2) blocks frontend (group 3); group 4 removes the orphaned
compat surface (bu-g0y3m) and must land after the dashboard cut-over. Confirm
the target DB per the `butlers-db-host-topology` memory before any migration.

## 1. Predicate + fact write (spec: relationship-facts)

- [x] 1.1 Seed `prefers-channel` into `relationship.entity_predicate_registry`
  (`kind='override'`, `object_kind='literal'`, `cardinality='single'`) via relationship-chain
  migration `rel_022` (kept off `kind='contact'` to stay out of the memory identity-predicate
  rejection floor)
- [x] 1.2 `prefers-channel` assert path: single-valued supersession; retract on clear
  (`assert_prefers_channel` / `retract_prefers_channel` in `relationship_assert_fact.py`)
- [x] 1.3 Write-time validation against the entity's existing `has-handle`/`has-email`/`has-phone`
  facts. OQ2 RESOLVED — DEGRADE within the handle family: per-channel proof where the prefix
  taxonomy is reliable (email→has-email, phone/sms→has-phone, telegram→has-handle:`telegram:`),
  degrade every other handle channel (discord, linkedin, …) to "entity has ANY active has-handle"
  because `_ef_channel_helpers`/rel_019 only prefix telegram handles
- [x] 1.4 Unit tests: assert, supersede, retract, reject-unreachable, validation-degrade path
  (`roster/relationship/tests/test_prefers_channel.py` +
  `tests/migrations/test_prefers_channel_predicate_migration.py`)

## 2. Load-bearing resolution in notify (spec: core-notify)

- [x] 2.1 Resolve OQ1 — RESOLVED to path (a) (design recommendation): make `notify(channel=...)`
  OPTIONAL and resolve-in-tool. A forced channel always wins; when omitted with a `contact_id`,
  `notify()` calls `resolve_outbound_channel()`; when omitted with no contact_id it defaults to
  telegram (the historical owner-page channel), preserving back-compat for callers that relied on
  a channel always being present. Deterministic + testable; tool contract widened (optional arg),
  not broken.
- [x] 2.2 `resolve_outbound_channel(pool, contact_id, deliverable_channels)` helper in
  `src/butlers/identity.py`: prefers-channel ∩ deliverable set ∩ reachable, else
  telegram→email fallback (first deliverable+reachable). Reuses group-1's
  `_entity_has_reachability_fact` / `PREFERS_CHANNEL_PREDICATE` (no duplicated channel mapping);
  degrades to `None` on schema-not-ready / DB error so notify falls through to its default path
- [x] 2.3 Wired into `notify()` (`src/butlers/core_tools/_notifications.py`) before any
  channel-dependent validation; forced channel never overridden (preference consulted only when
  `channel is None`)
- [x] 2.4 Tests: `roster/relationship/tests/test_resolve_outbound_channel.py` (real-DB:
  honored-when-deliverable, email-pref-beats-telegram-default, skipped-when-not-deliverable=discord,
  skipped-when-unreachable, no-pref→telegram, no-pref→email, no-reachable→None, unknown/orphan
  contact→None) + `tests/daemon/test_notify_contact_id.py::TestNotifyChannelResolution`
  (forced-channel-wins, omitted+contact_id resolves, omitted+no-contact_id→telegram, resolver-None→telegram)

## 3. Dashboard cut-over (spec: dashboard-relationship)

- [ ] 3.1 `ContactChannelCard` preference control reads/writes `prefers-channel` via the fact API
- [ ] 3.2 Offer only channels the contact has a contact fact for
- [ ] 3.3 Update/replace `ContactChannelCard` tests; gate on eslint + tsc + vitest

## 4. Remove orphaned compat surface (spec: contacts-identity) — bu-g0y3m

- [ ] 4.1 Data migration: `contacts.preferred_channel` → `prefers-channel` facts
  (`src='migration'`, `verified=true`); log+skip rows with no resolvable `entity_id`; assert backfill parity
- [ ] 4.2 Remove `preferred_channel` from `ContactPatchRequest` + `patch_contact` handling;
  remove the endpoint if it serves no other field (`roster/relationship/api/{router,models}.py`)
- [ ] 4.3 Delete `usePatchContact` hook + its tests once no other field needs it (`frontend/src/hooks/use-contacts.ts`)
- [ ] 4.4 Migration: `DROP COLUMN public.contacts.preferred_channel` (core chain; guard cross-chain refs per `cross-chain-migration-drop-hazard` memory)
- [ ] 4.5 Full quality gate: ruff + frontend eslint/tsc/vitest + relevant pytest

## 5. Close-out

- [ ] 5.1 `openspec validate entity-keyed-preferred-channel`
- [ ] 5.2 Update `relationship-facts` / `core-notify` / `contacts-identity` main specs on archive
