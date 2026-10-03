## ADDED Requirements

### Requirement: Relationship briefing respects person posture

The Relationship briefing contribution SHALL include a person in its birthday highlights and interaction-gap highlights only when the person's posture is `active`, and the birthday-gift delegation seed SHALL count only `active` people. A failed posture read SHALL fail the producer query rather than default to `active`.

#### Scenario: Memorial person has no birthday highlight

- **WHEN** a memorial person's birthday is within the next 7 days
- **THEN** the contribution SHALL contain no birthday highlight for them and `_count_birthdays_on` for that date SHALL exclude them

#### Scenario: Quiet person is not nudged

- **WHEN** a quiet or no_contact person exceeds their stay-in-touch threshold
- **THEN** the contribution SHALL contain no interaction-gap highlight for them
