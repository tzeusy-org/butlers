## ADDED Requirements

### Requirement: Known-contact drops are marked on the stored filtered row

When a connector drops a message from a sender it recognises as a known contact, whether by label exclusion, connector-scope block, or global-scope skip, it SHALL record the filtered event with a `drop_context` object of the form `{"important_dropped": true, "basis": "<basis>"}` on the stored `full_payload`. The marker SHALL carry no message content, SHALL NOT be forwarded in a replayed `ingest.v1` envelope, and SHALL be absent for senders that are not known contacts.

#### Scenario: Known contact dropped by a block rule is marked

- **WHEN** a connector-scope block rule drops a message whose sender is a known contact
- **THEN** the filtered row's `full_payload.drop_context` equals `{"important_dropped": true, "basis": "known_contact"}`
- **AND** `full_payload.payload.raw` remains empty

#### Scenario: Stranger drop is not marked

- **WHEN** a rule drops a message whose sender is not a known contact
- **THEN** the filtered row has no `drop_context`

#### Scenario: Replay omits the marker

- **WHEN** a marked row is drained for replay
- **THEN** the submitted envelope contains no `drop_context` key
