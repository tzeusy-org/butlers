# Relationship Care Duties

## Purpose

Defines owner-declared care duties for dependents (school pickups, a parent's medication, pet care) and the per-occurrence carer the owner assumes, stored by the Relationship butler. This is the S1 slice of bu-s11n0s.13: declaration, storage and listing. Coverage warnings (the S2 job) stay deferred past the cutover and are out of this capability's current requirements.

## Requirements

### Requirement: Care duties are owner-declared, owner-private records about entities

The Relationship butler SHALL store care duties in `relationship.care_duties` and recorded occurrences in `relationship.care_occurrences`. Only the owner SHALL declare, assign, retire or read them, through the owner-gated dashboard API under `/api/relationship/care`. A duty's subject (the dependent) and any carer SHALL be live `public.entities` rows (including the `animal` type); neither SHALL ever be a user or principal. No model-reachable tool SHALL write these tables in this slice.

ID: REQ-relationship-care-duties-001
Source: bu-s11n0s.13; roster/relationship/MANIFESTO.md "Duty of Care"; vision.md one-principal rule; owner decision relationship-duty-of-care (2026-11-21)
Scope: v1-mandatory

#### Scenario: A non-owner is refused

- **WHEN** a caller without the owner role calls any `/api/relationship/care` route
- **THEN** the response MUST be 403 `owner_required` and nothing MUST be read or written

#### Scenario: A dependent or carer must be a live entity

- **WHEN** a duty or assignment names a subject or carer that does not exist, is merged, or is deleted
- **THEN** the write MUST be refused as not found and no row MUST be created

### Requirement: A duty has exactly one anchoring shape

A `calendar_anchored` duty SHALL carry a valid RFC 5545 `rrule` and no `interval_days`; a `completion_anchored` duty SHALL carry `interval_days` and no `rrule`. The database SHALL enforce the shape with a CHECK, and the API SHALL refuse a mixed or invalid shape before writing. Retiring a duty SHALL set `retired_at`, hide it and its occurrences from default listings, and keep its history.

ID: REQ-relationship-care-duties-002
Source: bu-s11n0s.13 design (care_duties); owner decision relationship-duty-of-care (2026-11-21)
Scope: v1-mandatory

#### Scenario: Mixed shape refused

- **WHEN** a duty supplies both `rrule` and `interval_days`, or neither required field for its kind
- **THEN** the API MUST refuse it with a stable code and the database MUST reject the same row

#### Scenario: Retire keeps history

- **WHEN** the owner retires a duty twice
- **THEN** the second call MUST be a no-op and the duty and its occurrence rows MUST remain stored

### Requirement: Occurrence assignment is idempotent and owner-wins

The owner's carer for one occurrence SHALL be stored once per `(duty_id, occurrence_start)`; repeating or changing the assignment SHALL update that row, not add one. A calendar-anchored duty SHALL accept only a start its rule produces; a completion-anchored duty SHALL accept a start at or after its anchor. The owner-settable states SHALL be `assumed` and `confirmed`; the table's other states are reserved for the deferred coverage job, which SHALL NOT overwrite an owner assignment.

ID: REQ-relationship-care-duties-003
Source: bu-s11n0s.13 behavior matrix (idempotent upsert, owner assignments never overwritten)
Scope: v1-mandatory

#### Scenario: Reassignment replaces, never duplicates

- **WHEN** the owner assigns carer A then carer B to the same occurrence
- **THEN** exactly one row MUST exist and it MUST name carer B

### Requirement: Care duties never contact anyone and never claim coverage

Nothing in this capability SHALL send a message to a carer or dependent, call a connector, or invoke another butler. Carers SHALL NOT be contacted without per-event owner approval, which is outside this slice. The occurrence listing SHALL report only what the owner recorded and SHALL NOT report an occurrence as covered or uncovered.

ID: REQ-relationship-care-duties-004
Source: roster/relationship/MANIFESTO.md "Duty of Care"; bu-s11n0s.13 non-goals; S2 deferral (RFC 0038 cross-butler read)
Scope: v1-mandatory

#### Scenario: The listing makes no coverage claim

- **WHEN** the owner lists occurrences in a window
- **THEN** each row MUST carry only an owner-recorded state and no coverage inference

#### Scenario: The tool module has no outbound dependency

- **WHEN** the care-duties tool module is imported
- **THEN** it MUST import only the standard library and the RFC 5545 rule parser
