## ADDED Requirements

### Requirement: Relationship posture tool and nudge suppression

The Relationship butler SHALL register `entity_set_posture(entity_id, posture)` in its `entity` tool group. The insight scan (upcoming dates, stale contacts, pending gifts, interaction milestones) and `contacts_overdue` SHALL consider only people whose posture is `active`.

#### Scenario: Posture tool is registered

- **WHEN** the Relationship butler's `entity` tool group is enabled
- **THEN** `entity_set_posture` SHALL be registered

#### Scenario: Insight scan skips non-active people

- **WHEN** a person is memorial, quiet or no_contact and has an upcoming birthday
- **THEN** the insight scan SHALL propose no candidate about them
