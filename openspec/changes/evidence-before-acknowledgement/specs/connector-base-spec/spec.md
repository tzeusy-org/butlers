## ADDED Requirements

### Requirement: Evidence Before Acknowledgement

A connector SHALL commit a durable, connector-owned evidence row for an event before it
acknowledges that event, advances its checkpoint or cursor, or submits it to Switchboard.
`switchboard.message_inbox` is a routing buffer, never an evidence surface: no connector
SHALL rely on it as the verbatim copy of what arrived. A source with no evidence table of its
own SHALL write `connectors.evidence_journal`; a source with a dedicated evidence table
(`connectors.owntracks_points`, the Spotify and Steam tables) keeps that table as its surface.
The evidence row SHALL hold no more than the envelope's `message_inbox.raw_payload` would hold
at the envelope's ingestion tier, after bearer scrubbing; a tier-3 or policy-blocked event
that is never submitted SHALL write no row.

ID: REQ-connector-base-spec-015
Source: owner decision run24-inbox-not-archive (bu-uhbhzq.4, 2026-11-22); RFC 0014 D2; REQ-connector-owntracks-001
Scope: v1-mandatory

#### Scenario: A failed evidence write acknowledges nothing

- **WHEN** the evidence write for an event fails
- **THEN** the connector SHALL NOT submit the event, advance its checkpoint or cursor, or acknowledge it to the provider
- **AND** the event SHALL be held and retried; it SHALL NOT be parked, counted as skipped, or charged against a retry budget, because the park path is for submit failures only
- **AND** a connector with no database pool SHALL refuse to start rather than acknowledge without evidence

#### Scenario: A failed submit keeps the evidence

- **WHEN** the evidence row is committed and the Switchboard submit then fails
- **THEN** the evidence row SHALL remain
- **AND** the existing park and replay path SHALL deliver the event, with no second replay queue

#### Scenario: Duplicates collapse and revisions append

- **WHEN** the same `(source, endpoint_identity, provider_id, provider_revision)` is delivered again
- **THEN** exactly one journal row SHALL exist for it
- **AND** a new `provider_revision` SHALL append a second row

#### Scenario: The journal is append-only

- **WHEN** a statement updates, deletes or truncates `connectors.evidence_journal`
- **THEN** the database SHALL refuse it until a per-source retention contract provides a sanctioned deleter
