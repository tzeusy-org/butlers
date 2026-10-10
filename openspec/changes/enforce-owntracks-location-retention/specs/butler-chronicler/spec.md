## MODIFIED Requirements

### Requirement: Storage Shape

- The Chronicler schema SHALL contain point events, episodes, **episode-entity links**, episode-event links, overrides, projection checkpoints, source adapter state, and idempotency keys. Episodes and point events SHALL retain their existing shape, with two additions: every episode MUST carry a `layer` (`intent` | `evidence` | `activity`) and every `activity`-layer episode MUST carry a `confidence` (`high` | `medium` | `low`) and `evidence_refs[]`. Overlapping episodes SHALL remain permitted.
- Chronicler SHALL add own policy, exact per-source-adapter coverage, immutable plans/ready grants, source tombstones and durable run/holder receipts as defined in the proposed schema allocation. Native output/links/carryover/checkpoint/coverage SHALL commit together. Movement path_m SHALL be genuinely cumulatively computed before source forgetting; existing IDs/start/end/duration/layer/confidence/privacy/entity links SHALL survive. Spatial precision/floor SHALL be separate from temporal Precision and evidence_refs SHALL retain list[str], with typed reduced-evidence metadata rather than raw-coordinate title copies.

ID: REQ-butler-chronicler-003
Source: bu-s11n0s.7 original S2; RFC 0014 D1/D3/D4; openspec/specs/butler-chronicler/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Point events and episodes separated

- **WHEN** a source adapter projects evidence
- **THEN** instantaneous evidence SHALL be written to `point_events`
- **AND** span-shaped evidence SHALL be written to `episodes`
- **AND** every row SHALL carry `source_name`, `source_ref`, `precision`,
  `privacy`, `retention_days`, and optional `tombstone_at`

#### Scenario: Episode records its layer and confidence

- **WHEN** an inferred activity is stored
- **THEN** its `layer` is `activity`
- **AND** it carries a `confidence` and links to its corroborating evidence

#### Scenario: Overlapping episodes permitted

- **WHEN** two episodes from different sources cover overlapping time
- **THEN** both SHALL be stored
- **AND** neither SHALL be merged or discarded at storage time
- **AND** overlap queries SHALL return both

#### Scenario: Idempotent replay

- **WHEN** a source adapter re-projects the same source record
- **THEN** the stored row SHALL be updated in place via its
  `(source_name, source_ref)` idempotency key
- **AND** no duplicate row SHALL be created
- **AND** episode-entity links for the replayed episode SHALL be
  replaced atomically (DELETE-then-INSERT within the same transaction)
  so attendee removals and additions on the upstream source propagate
  on the next adapter run

#### Scenario: Episode-entity link cardinality

- **WHEN** a multi-participant event (such as a Google Calendar meeting)
  is projected as an episode
- **THEN** exactly ONE row SHALL be inserted into `episodes` for the
  upstream event, keyed by `(source_name, source_ref)` as today
- **AND** every participant entity that the upstream source has already
  resolved SHALL appear as a row in `episode_entities` referencing the
  episode's `id` and the participant's `entity_id`
- **AND** the participant's role SHALL be recorded as one of `'owner'`,
  `'organizer'`, or `'participant'`
- **AND** unresolved attendees (no entity match in `public.entities`)
  SHALL NOT create rows; they remain visible in the source-side
  payload but not in the chronicler join

#### Scenario: Episode entity link table contract

- **WHEN** the `chronicler.episode_entities` table is created
- **THEN** its primary key SHALL be `(episode_id, entity_id)` to
  enforce per-attendee idempotency
- **AND** `episode_id` SHALL have an ON DELETE CASCADE reference to
  `chronicler.episodes(id)` so episode tombstones cascade to the join
- **AND** the table SHALL NOT enforce a foreign key on `entity_id`
  against `public.entities(id)`, so chronicler boots in deployments
  where the relationship butler schema is not yet wired

#### Scenario: Missing upstream join table degrades gracefully

- **WHEN** a calendar adapter runs against a butler schema whose
  upstream `calendar_event_entities` table is absent (calendar module
  not installed on this deployment)
- **THEN** the adapter SHALL emit a debug-level log
- **AND** it SHALL write only the owner row (if resolvable) into
  `episode_entities`
- **AND** it SHALL NOT raise

#### Scenario: Multi-entity does NOT cross the LLM-free boundary

- **WHEN** the calendar adapter resolves participants
- **THEN** it SHALL read the entity set already resolved by the calendar
  module's `_upsert_event_entities` write path
- **AND** it SHALL NOT invoke an LLM for attendee classification or
  entity resolution
- **AND** the no-per-event-LLM invariant from RFC 0014 §D5 SHALL hold

#### Scenario: Storage Shape preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** Chronicler SHALL add own policy, exact per-source-adapter coverage, immutable plans/ready grants, source tombstones and durable run/holder receipts as defined in the proposed schema allocation. Native output/links/carryover/checkpoint/coverage SHALL commit together. Movement path_m SHALL be genuinely cumulatively computed before source forgetting; existing IDs/start/end/duration/layer/confidence/privacy/entity links SHALL survive. Spatial precision/floor SHALL be separate from temporal Precision and evidence_refs SHALL retain list[str], with typed reduced-evidence metadata rather than raw-coordinate title copies.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
