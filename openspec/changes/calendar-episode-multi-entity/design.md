## Doctrine Alignment

This change touches chronicler's authoritative storage shape, so it must
stay aligned with the load-bearing doctrine in `about/heart-and-soul/` and
`about/legends-and-lore/rfcs/`. The relevant passages and how this design
honors them:

- **Non-Negotiable Rule 1 — single-owner data sovereignty**
  (`about/heart-and-soul/vision.md:60-63`). All `entity_id` UUIDs continue
  to resolve to `public.entities`, the owner-scoped graph the memory
  butler owns. Multi-entity tagging does not introduce per-recipient
  visibility partitioning or any multi-tenant semantics. Episodes remain
  the owner's record of their own life.

- **Non-Negotiable Rule 3 — MCP-only inter-butler communication**
  (`about/heart-and-soul/vision.md:71-78`). Chronicler continues to read
  butler schemas via migration-tracked queries (the existing RFC 0010
  exception). The participant entity set is read from the calendar
  module's existing `calendar_event_entities` table inside the butler
  schema that hosts the calendar module; no new MCP call is introduced.
  No new cross-schema write is introduced.

- **Non-Negotiable Rule 4 — daemon is deterministic; intelligence is
  ephemeral** (`about/heart-and-soul/vision.md:80-84`). Attendee → entity
  resolution is owned by the calendar module's `_upsert_event_entities`
  path at write time. Chronicler's adapter does no LLM work, no fuzzy
  matching, no second-pass classification. It mirrors the already-resolved
  join.

- **RFC 0014 §D1 — episodes are spans of time, not per-participant rows**
  (`about/legends-and-lore/rfcs/0014-chronicler-time-butler.md:64-70`).
  A meeting is one episode whose evidence happens to involve N people.
  This design preserves the one-episode-per-meeting semantic and adds a
  join table for participants. Option B (one row per participant) would
  violate this passage.

- **RFC 0014 §D1 + butler-chronicler "Idempotent replay" — replays
  upsert in place via `(source_name, source_ref)`**
  (`about/legends-and-lore/rfcs/0014-chronicler-time-butler.md:81-82`,
  `openspec/specs/butler-chronicler/spec.md:75-79`). This invariant is
  preserved unchanged. The `episode_entities` rows for a given
  `episode_id` are replaced atomically on each adapter run; the
  canonical `episodes` row itself is not touched if its non-entity fields
  did not change.

- **RFC 0014 §D3 — adapters preserve provenance and degrade gracefully**
  (`about/legends-and-lore/rfcs/0014-chronicler-time-butler.md:135-146`).
  When the calendar module's `calendar_event_entities` table is missing
  in a deployment (calendar module not installed), the adapter falls back
  to writing only the owner entity into `episode_entities`, matching the
  current single-entity behavior. The adapter never raises on missing
  upstream tables.

- **butler-chronicler §"Storage Shape" — `episode_event_links` is the
  designated many-to-many surface for evidence, not entities**
  (`openspec/specs/butler-chronicler/spec.md:53-79`). The new join table
  is an entity-tag surface, not an evidence-link surface. It is parallel
  to `episode_event_links`, not a replacement. This keeps the
  evidence-linking story (a meeting's "started"/"ended" point events)
  separate from the participation story.

Doctrine pass: PASS. No rule conflicts. No spec rewrite of the
idempotency contract is required. The only doctrine-adjacent decision is
how to preserve the read-side experience during the deprecation window of
the existing `episodes.entity_id` column — addressed under "Migration
Sequencing" below.

## Context

`chronicler.episodes` currently has a single nullable `entity_id UUID`
column (migration `chronicler_013`, bu-f4755). The
`CalendarCompletedAdapter` resolves the owner entity once per schema via
the lookup chain:

```
{schema}.calendar_sources.metadata->>'account_email'
  → public.google_accounts.entity_id
```

For meetings with attendees, the upstream calendar module already
resolves attendees to entities and persists the result in the schema's
`calendar_event_entities` junction table (see
`openspec/specs/module-calendar/spec.md:147-159`). The chronicler
projection ignores this join and only writes the owner entity. The
entity-activity aggregator
(`GET /api/butlers/relationship/entities/{id}/activity`, shipped by
bu-aqe7n) therefore returns zero calendar episodes for any non-owner
entity, even when the meeting clearly involved them.

The implementation bead `bu-j6rqm` (currently blocked on this change)
asks an implementation worker to choose between (A) a join table or (B)
one episodes row per participant. The choice has downstream consequences
in chronicler dedup, the corrected view, the API contract, and the
dashboard activity surface.

Entity-redesign epic `bu-uhjxr` is mid-flight, replacing the
`public.contacts` / `public.contact_info` storage with relationship-owned
triples. The cut-over does NOT alter `public.entities`, so this change
is independent of the entity-redesign migration order.

## Goals

- One row per meeting in `chronicler.episodes`, regardless of attendee
  count.
- Participants visible to entity-activity reads via a deterministic join
  table.
- Dedup invariant on `(source_name, source_ref)` preserved unchanged.
- Adapter remains LLM-free, idempotent, and graceful on missing schemas.
- No breaking change to the `/api/chronicler/episodes` response shape;
  the new `participant_entity_ids` field is additive.
- Backfill story decoupled from schema migration so each can ship
  independently.

## Non-Goals

- This change does NOT introduce new entity-resolution policy. The
  calendar module owns attendee → entity resolution; chronicler reads
  what already exists.
- This change does NOT replace `episode_event_links` (evidence links are
  a separate concern).
- This change does NOT change the dashboard chronicles page layout
  (Gantt, scrubber, drawer remain unchanged). Privacy semantics
  (normal / sensitive / restricted) remain unchanged.
- This change does NOT alter the day-close bundle's token-bound shape
  (`MAX_TIER_2_INPUT_BYTES`). The bundle continues to cite source refs
  and may surface participant entity counts but not full entity payloads.
- This change does NOT modify the Google Calendar connector contract.

## Decisions

### D1: Join Table over Multi-Row Episodes

A new table `chronicler.episode_entities` is the multi-entity surface.
Schema:

```sql
CREATE TABLE chronicler.episode_entities (
    episode_id  UUID NOT NULL REFERENCES chronicler.episodes(id)
                  ON DELETE CASCADE,
    entity_id   UUID NOT NULL,
    role        TEXT NOT NULL DEFAULT 'participant'
                  CHECK (role IN ('owner', 'participant', 'organizer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (episode_id, entity_id)
);

CREATE INDEX episode_entities_entity_idx
    ON chronicler.episode_entities (entity_id, episode_id);
```

Notes:
- No FK to `public.entities(id)` (mirrors the existing convention from
  migration `chronicler_013`, which deliberately avoids the FK because
  chronicler may run before the relationship butler schema is wired).
  Application layer enforces existence.
- `role` is a thin enum: `'owner'` is the calendar owner (the Google
  account holder), `'organizer'` is the meeting organizer when distinct
  from the owner, `'participant'` is any other attendee resolved to an
  entity. Unresolved attendees do NOT create rows.
- Composite PK on `(episode_id, entity_id)` makes the upsert idempotent
  per attendee. Consequence: each `entity_id` appears at most once per
  episode, so the adapter MUST collapse multiple roles for the same
  entity to a single row using the precedence `'owner' > 'organizer' >
  'participant'` (highest role wins). See D3 step 4 for the deterministic
  rule.

### D2: Why Option B (Multi-Row) Is Rejected

Option B emits one `episodes` row per participant entity. To preserve
the existing `(source_name, source_ref)` uniqueness invariant, B has
two sub-options and both are unacceptable:

- **B1 — Composite source_ref**: encode participant into the ref string
  (e.g. `calendar:{origin_instance_ref}:{entity_id}`). This breaks
  every existing reader that joins on `source_ref` (notably the
  dashboards, the activity aggregator, and any future evidence-link
  insertion), fragments overlap queries across N rows per meeting, and
  inflates day-close bundle counts by attendee count, blowing the
  Tier-2 token cap on dense days.
- **B2 — Drop the uniqueness invariant**: extend the constraint to
  `(source_name, source_ref, entity_id)`. This forces a schema change
  on every adapter (sessions, spotify, owntracks, etc.) for a problem
  only calendar has, AND it makes `episode_event_links` ambiguous —
  which of the N rows does a `boundary_start` point event link to?

Option A (join table) avoids all of this with a single new table and
zero changes to existing adapter contracts.

### D3: Adapter Behavior

`CalendarCompletedAdapter` (now multi-entity aware) per upstream
`origin_instance_ref`:

1. Upsert one row into `chronicler.episodes` keyed on
   `(source_name='google_calendar.completed', source_ref='calendar:{ref}')`.
2. Resolve the owner entity from the schema's `calendar_sources` →
   `public.google_accounts` chain (unchanged from today).
3. Resolve the participant entity set by reading the schema's
   `calendar_event_entities` table joined through `calendar_events.id`
   (the calendar module's `event_id`, NOT chronicler's `episode_id`).
4. In a single transaction on the chronicler pool, DELETE existing
   `episode_entities` rows for this `episode_id` and INSERT the new
   set. The adapter builds the row set in two passes to enforce the
   role-precedence invariant `'owner' > 'organizer' > 'participant'`:
   - First collect candidate `(entity_id, role)` tuples from each
     upstream signal: the resolved owner entity contributes
     `(owner_id, 'owner')`; the resolved organizer entity (when
     distinct and the calendar event metadata flags it) contributes
     `(organizer_id, 'organizer')`; every other resolved attendee
     contributes `(attendee_id, 'participant')`.
   - Then collapse by `entity_id` keeping the highest-precedence role,
     so an attendee who is also the owner is written once with
     `role='owner'` and never collides on the composite primary key.
   The collapsed set is what gets INSERTed.
5. Write the owner UUID to `episodes.entity_id` so existing readers
   that have not migrated continue to work for one release cycle.

The DELETE-then-INSERT is intentional: it lets attendee removals from
the upstream calendar propagate without an extra query to compute the
diff. Because the chronicler pool is the only writer for
`episode_entities` and `episode_event_links` (no cross-butler writers
to this schema), there is no contention concern.

The adapter degrades gracefully:
- If `calendar_event_entities` is absent (older deployment, calendar
  module not installed), the adapter writes only the owner row, matching
  current behavior. A debug-level log entry records the degradation.
- If `public.google_accounts` is absent or the email lookup fails, no
  `episode_entities` rows are written; the episode is still projected
  with `entity_id = NULL`. This matches current behavior.

### D4: Idempotency and Replay

The `(source_name, source_ref)` uniqueness invariant on
`chronicler.episodes` is preserved unchanged. A replay (watermark reset
or backfill) of the same `origin_instance_ref` upserts the canonical
`episodes` row in place and replaces the `episode_entities` rows.

This matches the upstream calendar module's own pattern (full-replace
on `_upsert_event_entities` per `openspec/specs/module-calendar/spec.md:137-145`):

- Upstream: "existing entity links for the event are replaced with the
  new set (full replace, not additive)".
- Chronicler: same. Full replace per episode.

### D5: Entity-Merge Re-Pointing

When the memory butler merges two entities via `memory_entity_merge`, the
existing `_repoint_calendar_event_entities` helper in
`src/butlers/modules/memory/tools/entities.py` updates the calendar
module's join table. This change adds a parallel
`_repoint_episode_entities` helper that:

1. Deletes any chronicler `episode_entities` rows that would collide
   with the target (`episode_id, target_entity_id` already exists).
2. Updates the remaining rows from source to target entity.
3. Also updates `chronicler.episodes.entity_id` where it equals the
   source entity UUID (the derived-owner column).

Re-pointing is performed via the existing cross-schema write surface the
memory module already uses for `calendar_event_entities`. No new RFC
exception is needed; the precedent is established.

### D6: API Contract Impact

The chronicler API delta is additive:

- `GET /api/chronicler/episodes` and
  `GET /api/chronicler/episodes/{id}` add a `participant_entity_ids`
  array to each episode object. The existing `entity_id` field is
  preserved during the transition window and equals the owner row.
- The `v_episodes_corrected` view is recreated to add the aggregated
  `participant_entity_ids` column (a `UUID[]` produced by
  `array_agg(entity_id) FILTER (...)`).
- Entity-activity callers (currently the relationship butler's
  `GET /api/butlers/relationship/entities/{id}/activity`) MUST switch
  to filtering via the join table; the existing single-column filter
  remains valid for one release.

The chronicler API SHALL accept a new optional filter on
`GET /api/chronicler/episodes`:

```
?participant_entity_id=<uuid>
```

When supplied, the query joins `episode_entities` and returns episodes
that have the given entity in any role. The existing `entity_id`
parameter (owner-only) is preserved during transition.

### D7: Dashboard Surface Impact

The chronicles dashboard page is owner-scoped today and does not filter
by participant. No layout or behavior change is required for the
chronicles page itself. The relationship butler's entity detail page
(per `dashboard-relationship` and bu-uhjxr's tab redesign) consumes the
entity-activity aggregator; that aggregator is the surface that benefits
from the join.

If a future iteration wants to show "shared meetings with X" on the
entity detail page, that capability is unlocked by this change but is
explicitly out of scope.

### D8: Cut-Over Sequencing Against bu-uhjxr

`bu-uhjxr` is the contacts → triples migration. It:
- Reshapes `public.contacts` and `public.contact_info` write paths.
- Does NOT alter `public.entities`.
- Does NOT alter `public.google_accounts.entity_id`.
- Does NOT alter chronicler's schema.

The two changes are therefore independent. This change MAY land before,
during, or after the entity-redesign cut-over. The only soft dependency
is that the entity-activity aggregator endpoint shape change (filtering
through the join table) MUST not regress when bu-uhjxr's entity detail
page change lands.

Concrete sequencing recommendation:
1. Land the schema migration (`chronicler_014`) and view rebuild.
2. Land the adapter change (writes the join table; preserves derived
   owner column).
3. Land the API additive field (`participant_entity_ids`).
4. Land the entity-activity aggregator switch to join-based filter.
5. Land the backfill script in a separate bead so the schema migration
   can ship independently.
6. After two release cycles, drop the derived `episodes.entity_id`
   column in a follow-up cleanup bead.

### D9: Backfill Story

Backfill is split into two scripts shipped as separate beads:

- **Forward backfill** (default path): reset the
  `projection_checkpoints` watermark for `google_calendar.completed`,
  let the adapter re-project all historical meetings. Each rerun
  re-resolves participants from the current upstream join, so the join
  table converges naturally without bespoke code.

- **Targeted backfill** (no watermark reset): a script analogous to
  `scripts/backfill_episode_entity_id.py` that walks every existing
  `chronicler.episodes` row with `source_name='google_calendar.completed'`,
  derives the schema and `origin_instance_ref`, reads the upstream
  attendee set from `calendar_event_entities`, and writes the rows
  directly without invoking the adapter. Idempotent; safe to re-run.

The targeted backfill is recommended for production because the
forward backfill re-projects every episode (re-writing titles,
re-running the dedup pass), which is more I/O than necessary for an
entity-only enrichment.

### D10: Telemetry

- Adapter SHALL emit a counter
  `chronicler_episode_participants_resolved_total{schema}` per adapter
  run, recording the number of participant rows written.
- Adapter SHALL emit a debug log `episode_entities.schema_absent` when
  the upstream `calendar_event_entities` table is missing.
- API SHALL emit existing OTel spans on the new
  `participant_entity_id` filter path with span attribute
  `chronicler.episodes.filter_kind=participant_join` to distinguish
  from `owner_column`.

## Risks

- **Backfill cost**: the targeted backfill walks N meetings. For a
  multi-year owner timeline this is on the order of low-thousands of
  rows, comfortably within a single-process script. Forward backfill via
  watermark reset is heavier (re-projects all episodes) but matches the
  existing operator playbook in `roster/chronicler/AGENTS.md`. Both
  paths are documented in `tasks.md` so the operator can choose.

- **Adapter run-time cost**: per-schema execution now adds one extra
  query per upstream meeting (read `calendar_event_entities`). For a
  user with one Google account schema and ~10 new meetings per day,
  this is negligible. For deployments with multiple schemas the cost
  scales linearly. A batched-read variant (one query per schema joining
  all instances) is a follow-up optimization, not a blocker.

- **Derived `episodes.entity_id` drift**: during the transition window,
  the derived column could go stale if the join table is updated but
  the column is not. The adapter must update both in the same
  transaction. The cleanup bead that drops the column after two release
  cycles closes this risk window permanently.

- **Privacy carry-forward**: episodes inherit privacy from the source
  declaration. Adding participant entity IDs does NOT escalate
  visibility: a `restricted` episode is still filtered server-side and
  never returned, regardless of who its participants are. The chronicles
  privacy contract in `roster/chronicler/AGENTS.md` is unchanged.

- **Cross-schema fan-out**: the existing adapter already dedups across
  schemas via `seen_origin`. The "first schema wins" rule for owner
  resolution is preserved; for participants, the union of attendees
  from any schema that projected the meeting becomes the join set. In
  practice every schema sees the same upstream attendees, so the union
  collapses to one set.

- **Entity-redesign timing**: if `bu-uhjxr` lands first, this change is
  unaffected. If this change lands first, `bu-uhjxr`'s changes to
  contact-keyed APIs do not touch chronicler. The two are decoupled.

## Open Questions

- **Should the join include past-attendee history?** Current proposal:
  no. The join reflects the current upstream attendee set. If an
  attendee is removed from a meeting upstream, the next adapter run
  removes them from the join (DELETE-then-INSERT semantics). If the
  product wants "who was originally invited" history, that is a
  separate audit-log surface and out of scope for v1 of this change.
- **Role refinement**: do we need `role IN ('owner', 'organizer',
  'participant', 'optional', 'resource')` to mirror Google Calendar's
  attendee classifications? Current proposal: start with three roles;
  expand the CHECK constraint in a follow-up if the dashboard surfaces
  the distinction.
