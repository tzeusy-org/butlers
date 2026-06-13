## MODIFIED Requirements

### Requirement: calendar_event_entities Junction Table

The `calendar_event_entities` table SHALL be the cross-reference
between `calendar_events` rows and entities in the memory butler's
entity graph. It MUST enable reverse lookup — given an entity, find all
calendar events associated with it — and it SHALL be the authoritative
source of participant entity membership for downstream retrospective
projection by the Chronicler butler.

Schema: `(event_id UUID REFERENCES calendar_events(id) ON DELETE CASCADE,
entity_id UUID REFERENCES public.entities(id) ON DELETE CASCADE)` with a
UNIQUE constraint on `(event_id, entity_id)`.

#### Scenario: Entity merge re-pointing

- **WHEN** two entities are merged via `entity_merge()` in the memory module
- **THEN** `calendar_event_entities` rows referencing the source entity are
  re-pointed to the target entity
- **AND** any duplicates created by the re-point are deleted
  (deduplication on `(event_id, entity_id)`)
- **AND** failures in the re-pointing step are swallowed gracefully if
  the table does not exist
- **AND** the parallel chronicler join table
  `chronicler.episode_entities` is re-pointed in the same
  `entity_merge()` flow so the two surfaces do not drift; see
  `butler-chronicler` for the chronicler-side requirement

#### Scenario: Authoritative source for chronicler participant resolution

- **WHEN** the Chronicler `CalendarCompletedAdapter` projects a
  completed calendar instance into a `chronicler.episodes` row
- **THEN** the adapter SHALL read the upstream attendee → entity
  resolution from `{schema}.calendar_event_entities` joined through
  `calendar_events.id` (the upstream event row), NOT from the raw
  Google Calendar attendee payload
- **AND** the calendar module SHALL remain the sole writer to
  `calendar_event_entities`; chronicler SHALL NOT mutate or re-resolve
  attendees on its own
- **AND** when the upstream `calendar_event_entities` table is absent
  in a deployment (calendar module disabled), the chronicler adapter
  SHALL degrade gracefully by writing only the owner row into
  `chronicler.episode_entities` (see `butler-chronicler`)
- **BECAUSE** attendee → entity resolution is a write-time deterministic
  step owned by the calendar module's `_upsert_event_entities`; the
  chronicler retrospective view must reflect that decision rather than
  invent its own

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0006 (Database schema isolation)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler) §D3 Adapter Contract
