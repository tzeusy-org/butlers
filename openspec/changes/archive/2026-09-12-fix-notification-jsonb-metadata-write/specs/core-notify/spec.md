## ADDED Requirements

### Requirement: Notification Delivery Metadata Object Persistence

The Switchboard production notification-delivery writer SHALL normalize optional
metadata to a JSON-safe mapping and bind that mapping directly through the
registered asyncpg JSONB codec. It SHALL NOT pre-serialize the mapping to JSON
text before binding it. Every newly written `switchboard.notifications.metadata`
value SHALL therefore be a JSONB object. This requirement does not repair or
reinterpret pre-existing string-shaped metadata rows.

#### Scenario: Structured metadata is written as an object

- **WHEN** a normal notification delivery write includes representative
  structured metadata
- **THEN** `jsonb_typeof(notifications.metadata)` is `object`
- **AND** the stored metadata preserves the mapping's JSON-safe content
