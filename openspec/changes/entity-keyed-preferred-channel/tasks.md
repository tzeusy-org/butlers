# Tasks — entity-keyed-preferred-channel

Backend (groups 1–2) blocks frontend (group 3); group 4 removes the orphaned
compat surface (bu-g0y3m) and must land after the dashboard cut-over. Confirm
the target DB per the `butlers-db-host-topology` memory before any migration.

## 1. Predicate + fact write (spec: relationship-facts)

- [ ] 1.1 Seed `prefers-channel` into `relationship.entity_predicate_registry`
  (`object_kind='literal'`, `cardinality='single'`) via relationship-chain migration
- [ ] 1.2 `prefers-channel` assert path: single-valued supersession; retract on clear
- [ ] 1.3 Write-time validation against the entity's existing `has-handle`/`has-email`/`has-phone`
  facts; resolve OQ2 (handle channel-prefix reliability) first — degrade to "has any handle" if needed
- [ ] 1.4 Unit tests: assert, supersede, retract, reject-unreachable, validation-degrade path

## 2. Load-bearing resolution in notify (spec: core-notify)

- [ ] 2.1 Resolve OQ1 (optional `channel` + resolve-in-tool vs. inject-into-context) at sign-off
- [ ] 2.2 `resolve_outbound_channel(contact_id)` helper: prefers-channel ∩ deliverable set,
  else existing `has-handle`→`has-email` fallback (`src/butlers/identity.py` / core notify path)
- [ ] 2.3 Wire into `notify()` contact-targeted path without changing forced-channel behavior
- [ ] 2.4 Tests: honored-when-deliverable, skipped-when-not-deliverable (discord), no-pref fallback, forced-channel-wins

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
