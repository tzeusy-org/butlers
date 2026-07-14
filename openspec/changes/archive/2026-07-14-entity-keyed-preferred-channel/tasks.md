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

- [x] 3.1 `ContactChannelCard` preference control reads/writes `prefers-channel` via the fact API
  (entity-keyed `PUT`/`DELETE /entities/{id}/preferred-channel` →
  `assert_prefers_channel`/`retract_prefers_channel`; `useSetPreferredChannel`/
  `useClearPreferredChannel`; `list_entity_linked_contacts` now sources
  `preferred_channel` from the `prefers-channel` fact, not `public.contacts`)
- [x] 3.2 Offer only channels the contact has a contact fact for
  (`reachable_channels` on `LinkedContactSummary`, derived server-side from the
  entity's `has-email`/telegram-prefixed `has-handle` facts; selector gates options)
- [x] 3.3 Update/replace `ContactChannelCard` tests; gate on eslint + tsc + vitest
  (new `ContactChannelCard.preferred-channel.test.tsx`; backend
  `test_relationship_preferred_channel.py` +
  `test_relationship_entities_linked_contacts.py` preferred-channel coverage)

## 4. Remove orphaned compat surface (spec: contacts-identity) — bu-g0y3m

- [x] 4.1 Data migration: `contacts.preferred_channel` → `prefers-channel` facts
  (`src='migration'`, `verified=true`); log+skip rows with no resolvable `entity_id`; assert backfill parity
  (`alembic/versions/core/core_123_drop_contacts_preferred_channel.py` step 3: snapshot + assert-as-fact + parity guard)
- [x] 4.2 Remove `preferred_channel` from `ContactPatchRequest` + `patch_contact` handling;
  remove the endpoint if it serves no other field (no `ContactPatchRequest`/`patch_contact` remain in
  `roster/relationship/api/{router,models}.py`; frontend `ContactPatchRequest` no longer carries the field,
  `frontend/src/api/types.ts:1986` "preferred_channel is NOT writable here")
- [x] 4.3 Delete `usePatchContact` hook + its tests once no other field needs it. N/A: the orphaned
  preferred-channel write path was removed (moved to `useSetPreferredChannel`/`useClearPreferredChannel`,
  `use-entities.ts`); the `usePatchContact` hook is correctly RETAINED because it still serves other
  contact fields (full_name/first_name/company/roles), which is exactly the task's "once no other field
  needs it" gate
- [x] 4.4 Migration: `DROP COLUMN public.contacts.preferred_channel` (core chain; guard cross-chain refs per `cross-chain-migration-drop-hazard` memory)
  (`core_123` step 5; doubly mooted since `core_134_drop_public_contacts` later dropped the whole table)
- [x] 4.5 Full quality gate: ruff + frontend eslint/tsc/vitest + relevant pytest (shipped through the section-4 beads)

## 5. Close-out

- [x] 5.1 `openspec validate entity-keyed-preferred-channel` (passes `--strict`)
- [x] 5.2 Update `relationship-facts` / `core-notify` / `contacts-identity` main specs on archive
  (main specs ALREADY carry these deltas from the feature's implementation PRs: relationship-facts
  `prefers-channel predicate`, core-notify `Preferred-Channel Resolution on Omitted Channel`,
  dashboard-relationship `Contact channel preference is entity-keyed`, and the contacts CRM column is
  removed via core_123/core_134, so archive is a move-only, `--skip-specs`)
