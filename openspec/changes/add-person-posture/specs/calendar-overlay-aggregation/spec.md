## ADDED Requirements

### Requirement: Relationship overlay respects person posture

The Relationship calendar overlay job SHALL emit `birthday` and `important_date` entries only for `active` people. For a `memorial` person it SHALL emit a `remembrance` entry with `low` priority in place of the birthday and SHALL emit no other date entry. For `quiet` and `no_contact` people it SHALL emit no date entry.

#### Scenario: Memorial birthday becomes a remembrance

- **WHEN** a memorial person's birthday falls inside the lookahead window
- **THEN** the envelope for that date SHALL contain a `remembrance` entry and no `birthday` entry for them

#### Scenario: Quiet person's dates are omitted

- **WHEN** a quiet person has a birthday inside the window
- **THEN** no envelope SHALL contain an entry for them
