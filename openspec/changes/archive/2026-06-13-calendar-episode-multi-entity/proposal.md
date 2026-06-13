## Why

Calendar episodes in `chronicler.episodes` carry a single `entity_id` column
(added in migration `chronicler_013`, bu-f4755). For meetings that involve
multiple participants — owner, colleagues, family — the projection currently
only tags the owner entity (resolved from `public.google_accounts.entity_id`
via the schema's `calendar_sources.account_email`). The result is that a
non-owner participant's entity activity feed never surfaces the meeting that
involved them, even though the upstream calendar module already knows about
the attendee set via the `calendar_event_entities` junction table on the
calendar (module) side.

The implementation bead `bu-j6rqm` asks a worker to choose between two
shapes:

- **(A) Join table** — add `chronicler.episode_entities` linking many entities
  to one episode row. One episode per upstream calendar instance; multiple
  rows in the join table for participants.
- **(B) Multi-row episodes** — emit one `episodes` row per participant
  entity. The episode count fans out by the number of attendees.

Neither choice is obvious, and the design has downstream consequences
across the chronicler dedup invariant (`UNIQUE (source_name, source_ref)`),
the corrected view (`v_episodes_corrected`), the chronicler API contract
(entity-activity reads at `/api/butlers/relationship/entities/{id}/activity`,
already implemented for single-entity in bu-aqe7n), the dashboard activity
surfaces, the day-close bundle, and the entity-redesign cut-over (bu-uhjxr).
Per `/project-direction`, no implementation should land until this is
reconciled against doctrine and spec.

## What Changes

- **Recommend Option A (join table).** Introduce
  `chronicler.episode_entities (episode_id UUID, entity_id UUID, role TEXT,
  PRIMARY KEY (episode_id, entity_id))` and migrate the single-row
  `episodes.entity_id` column to this table during the cut-over. The
  existing `(source_name, source_ref)` uniqueness invariant on `episodes`
  is preserved unchanged.
- Update `CalendarCompletedAdapter` to insert one `episodes` row per
  upstream `origin_instance_ref` and populate `episode_entities` with the
  owner entity plus any participant entities resolvable from the calendar
  module's `calendar_event_entities` join (read via the schema's
  `calendar_event_entities` table, scoped through the parent
  `calendar_events.id → calendar_event_instances.event_id`). Adapter
  upsert remains keyed on `(source_name, source_ref)`.
- Preserve `episodes.entity_id` as a **derived owner column** for one
  release cycle to give entity-activity callers a transition window, then
  retire it after the dashboard surfaces migrate to the join. Document the
  derived semantics: `episodes.entity_id` SHALL equal the owner row in
  `episode_entities` where `role = 'owner'` if present, else NULL.
- Extend `v_episodes_corrected` with a `participant_entity_ids UUID[]`
  column populated by aggregating `episode_entities`. Read endpoints
  preserve their current shape; the new column is additive.
- Extend `chronicler_list_episodes` and the
  `/api/butlers/relationship/entities/{id}/activity` aggregator to filter
  by `episode_entities.entity_id` instead of (or in addition to) the
  single-column `episodes.entity_id`.
- Document the **dedup key collision** that the alternative Option B
  would have introduced and explicitly reject Option B.
- Document the **idempotency replay** story: when a meeting's attendee
  set changes upstream, the next adapter run replaces the
  `episode_entities` rows for that `episode_id` atomically. Canonical
  `episodes.entity_id` does not flip.
- Sequence the cut-over against `bu-uhjxr` (entity-redesign,
  contacts → triples). The chronicler join table refers to
  `public.entities(id)` UUIDs, which the entity-redesign does not alter;
  the change is therefore unblocked by, and independent of, the contacts
  → triples migration.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `butler-chronicler`: storage shape and idempotent replay rules MUST
  account for multi-entity participation on episodes.
- `chronicler-api`: episode read endpoints MUST expose participant entity
  IDs as response provenance; entity-activity reads MUST resolve via the
  join table.
- `module-calendar`: the calendar module is the upstream source of truth
  for participant entity resolution; chronicler reads from
  `calendar_event_entities` and does not re-resolve attendees itself.

## Recommendation Rationale

Option A (join table) is the correct choice because Option B (multi-row)
violates two load-bearing invariants the existing specs encode:

1. `butler-chronicler` §"Idempotent replay" requires
   `(source_name, source_ref)` to uniquely identify a projected row;
   Option B would either collide on the same `(source_name, source_ref)` key
   for every participant or require a composite key
   `(source_name, source_ref, entity_id)` that breaks every existing
   adapter and the `v_episodes_corrected` view's join semantics.
2. RFC 0014 §D1 declares episodes are "things that took a span of time" —
   a meeting is one such thing, not N. Emitting N rows fragments overlap
   queries, breaks `episode_event_links`, and inflates day-close bundle
   token counts by the participant count.

Option A localizes the change to one new table, leaves dedup invariants
intact, and matches the existing `calendar_event_entities` precedent in
the calendar module — chronicler simply mirrors the upstream join.

## Non-Goals

- This change does NOT redesign the entity model. Entity resolution stays
  in `public.entities` and continues to be owned by the memory butler.
- This change does NOT alter the chronicler API's existing endpoint set
  shapes beyond an additive `participant_entity_ids` array and the
  entity-activity filter expansion.
- This change does NOT introduce per-event LLM resolution of meeting
  attendees. The calendar module already does deterministic attendee →
  entity resolution at write time; chronicler reads what is already there.
- This change does NOT change Google Calendar connector behavior. The
  connector continues to ingest events with their attendee lists; entity
  resolution is the calendar module's job.
- This change does NOT migrate or backfill historical episodes in the
  same release as the schema change. Backfill is a separate downstream
  bead so the schema change can ship independently.

## Rollback Plan

- The new `chronicler.episode_entities` table is additive and downward
  compatible. If the adapter change has to be reverted, dropping the new
  table and restoring the single-column read path is a single migration.
- The derived `episodes.entity_id` column remains writable for one
  release cycle, so a rollback that drops the join table does not lose
  owner-entity tagging.
- `v_episodes_corrected` is recreated, not altered in place, so reverting
  is a `CREATE OR REPLACE VIEW` migration.

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty) — entity rows in
  `public.entities` remain owner-scoped; multi-entity tagging does not
  introduce multi-tenancy semantics.
- Non-Negotiable Rule 3 (MCP-only inter-butler communication) — chronicler
  continues reading from migration-tracked surfaces only; no MCP calls
  to the calendar module are introduced.
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral) — attendee → entity
  resolution remains deterministic and adapter-side.
- RFC 0006 (Database schema isolation) — chronicler reads from butler
  schemas via existing schema-qualified queries; no cross-schema writes.
- RFC 0010 (Cross-Butler Briefing Exception) — existing precedent for
  chronicler's cross-schema read pattern.
- RFC 0014 (Chronicler Time Butler) — §D1 storage shape and §D3 adapter
  contract preserved unchanged; only the entity-link cardinality changes.
