# Entity-Keyed Preferred Channel

## Why

`public.contacts.preferred_channel` (VARCHAR, CHECK in `'telegram' | 'email'`,
`alembic/versions/core/core_002_identity.py:151`) is **orphaned**: it is written
by exactly one path — the dashboard `usePatchContact` hook →
`PATCH /api/relationship/contacts/{id}` (`roster/relationship/api/router.py`,
`ContactChannelCard.tsx`) — and read by **nothing** in the runtime. `notify()`
and `resolve_contact_by_channel()` (`src/butlers/identity.py:131`) already pick
channels from `relationship.entity_facts` triples (`has-handle` / `has-email` /
`has-phone`), never from `contacts.preferred_channel`. The field is UI-only CRM
metadata that no butler consults when deciding where to send a message.

`ContactChannelCard.tsx:20` documents this as the **sole reason** the
`PATCH /contacts/{id}` endpoint and `usePatchContact` hook survive ("COMPAT-ONLY
… kept for this sole write path until a triple-based model is added"). Bead
`bu-g0y3m` is blocked on closing exactly this gap.

Migrating an orphaned field as pure storage would be busywork. The owner
decision (2026-06-13) is to **make channel preference real**: store it as an
entity-keyed fact AND have outbound targeting honor it. This both unblocks
`bu-g0y3m` and turns a dead CRM field into a working preference.

## What Changes

- **New `prefers-channel` predicate** in `relationship.entity_predicate_registry`
  — `object_kind='literal'`, object = a channel name. Single-valued per entity
  (cardinality `single`): asserting a new preference supersedes the prior active
  one. Expressible over **any** channel the entity has a contact fact for
  (telegram, email, discord, phone/SMS, …), not just `email|telegram` —
  validated at write time against the entity's existing `has-*` facts.
- **Load-bearing in `notify()`**: when a butler targets a `contact_id` without
  forcing a `channel`, outbound resolution consults the entity's active
  `prefers-channel` fact and uses it **when that channel is currently
  deliverable** (presently `telegram`/`email`, per `core-notify` Channel
  Validation), else falls back to today's `has-handle`/`has-email` precedence. A
  preference for a not-yet-deliverable channel (e.g. `discord`) is stored and
  surfaced but is a no-op for delivery until that channel is supported — no
  error.
- **Dashboard write path swap**: `ContactChannelCard` sets/clears the preference
  via the entity fact-assert path (the relationship fact API the rest of the
  card already uses), not `PATCH /contacts/{id}`.
- **Remove the orphaned compat surface**: drop `public.contacts.preferred_channel`,
  the `PATCH /api/relationship/contacts/{id}` endpoint, and the `usePatchContact`
  hook (this is `bu-g0y3m`, now unblocked). Migrate existing column values into
  `prefers-channel` facts first.

Non-goals (binding): no new deliverable channels are added to `notify()` (SMS,
discord, etc. remain out of scope here — preference can name them, delivery
can't yet reach them); no per-message channel override UX; no multi-channel
fan-out / "try telegram then email" cascade beyond the single existing fallback;
no LLM involvement in channel choice.

## Capabilities

### Modified Capabilities

- `relationship-facts`: add the `prefers-channel` predicate contract —
  single-valued-per-entity semantics, channel-name object, write-time validation
  against existing contact facts.
- `core-notify`: outbound channel resolution for a `contact_id` consults the
  `prefers-channel` fact, honored only when the channel is deliverable, with
  defined fallback.
- `dashboard-relationship`: the contact-channel preference control reads/writes
  the `prefers-channel` fact instead of the CRM column.
- `contacts-identity`: remove `contacts.preferred_channel`, the
  `PATCH /contacts/{id}` endpoint, and `usePatchContact` after data migration.

## Impact

- **DB**: new predicate seed row (relationship chain); data migration
  `contacts.preferred_channel` → `prefers-channel` facts; later migration to
  `DROP COLUMN contacts.preferred_channel`.
- **Backend**: channel-resolution helper in the `notify()` path
  (`src/butlers/core/` + `src/butlers/identity.py`); fact-assert validation for
  `prefers-channel`; removal of `patch_contact` preferred-channel handling and
  eventually the endpoint (`roster/relationship/api/router.py`, `models.py`).
- **Frontend**: `ContactChannelCard.tsx` preference control rewired to the fact
  API; delete `usePatchContact` (`frontend/src/hooks/use-contacts.ts`) and its
  tests once no other field needs it.
- **Unblocks**: `bu-g0y3m` (compat-only `usePatchContact` + `PATCH /contacts/{id}`
  removal).
- **Sole-user deployment**: no external readers of the column → no transition
  window required; migrate-then-drop can land in one change.
