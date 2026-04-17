## ADDED Requirements

### Requirement: Calendar event entity junction table

A `calendar_event_entities` junction table SHALL exist to link calendar events to entities. This table enables any calendar event (reminder, scheduled task, or provider-synced event) to be associated with zero or more entities.

```sql
CREATE TABLE calendar_event_entities (
    event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, entity_id)
);

CREATE INDEX idx_calendar_event_entities_entity
ON calendar_event_entities(entity_id);
```

The junction table resides in the same schema as `calendar_events` (butler-local schema). Both foreign keys use `ON DELETE CASCADE` — deleting an event removes all its entity associations, and deleting an entity removes all its event associations without deleting the events themselves.

#### Scenario: Entity deletion preserves calendar events

- **WHEN** an entity is deleted from `public.entities`
- **THEN** all rows in `calendar_event_entities` referencing that entity SHALL be deleted via CASCADE
- **AND** the `calendar_events` rows themselves SHALL NOT be deleted — they lose the association but remain as events

#### Scenario: Entity merge updates event associations

- **WHEN** entity A is merged into entity B via `entity_merge(source=A, target=B)`
- **THEN** `calendar_event_entities` rows with `entity_id = A` SHALL be re-pointed to `entity_id = B`
- **AND** duplicate `(event_id, entity_id)` pairs SHALL be deduplicated (if event was already linked to both A and B, keep one row)

#### Scenario: Dashboard entity detail shows associated events

- **WHEN** the dashboard entity detail page is rendered for entity X
- **THEN** a query joining `calendar_event_entities` with `calendar_events` filtered by `entity_id = X` SHALL return all calendar events associated with that entity
- **AND** events SHALL be displayed with title, start time, recurrence status, and source butler
