## ADDED Requirements

### Requirement: Relationship posture tool and nudge suppression

The Relationship butler SHALL register `entity_set_posture(entity_id, posture)` in its `entity_write` tool group. The insight scan (upcoming dates, stale contacts, pending gifts, interaction milestones) and `contacts_overdue` SHALL consider only people whose posture is `active`.

ID: REQ-butler-relationship-004
Source: Adopted add-person-posture; closed bu-t89d9m option A (2026-10-20 14:36 UTC) group amendment
Scope: v1-mandatory

#### Scenario: Posture tool is registered

- **WHEN** the Relationship butler's `entity_write` tool group is enabled
- **THEN** `entity_set_posture` SHALL be registered
- **AND** when only the read-only `entity` group is enabled,
  `entity_set_posture` SHALL NOT be registered

#### Scenario: Insight scan skips non-active people

- **WHEN** a person is memorial, quiet or no_contact and has an upcoming birthday
- **THEN** the insight scan SHALL propose no candidate about them
