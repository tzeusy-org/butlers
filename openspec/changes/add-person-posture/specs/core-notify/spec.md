## ADDED Requirements

### Requirement: Recipient posture refusal

`notify` SHALL, for an `entity_id`-targeted call, read the entity's posture before resolving a channel identifier, parking an approval or enqueuing a delivery. When the posture is `memorial` or `no_contact`, or the posture cannot be read, `notify` SHALL return `{"status": "error", "code": "recipient_posture"}` and SHALL NOT create a pending action, a delivery row or an owner notification. The refusal text and the attention-ledger row SHALL NOT contain the posture value. `quiet` and `active` SHALL NOT be refused by this gate.

#### Scenario: no_contact recipient is refused

- **WHEN** `notify(entity_id=<no_contact entity>)` is called
- **THEN** the result SHALL carry `code: recipient_posture` and no pending action or delivery row SHALL exist

#### Scenario: Unreadable posture fails closed

- **WHEN** the posture read raises
- **THEN** `notify` SHALL refuse with `code: recipient_posture` rather than treat the entity as active

#### Scenario: Quiet recipient is not refused here

- **WHEN** `notify(entity_id=<quiet entity>)` is called
- **THEN** the posture gate SHALL NOT refuse and ordinary recipient resolution and approval gating SHALL apply
