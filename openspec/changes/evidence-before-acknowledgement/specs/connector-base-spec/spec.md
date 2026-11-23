## ADDED Requirements

### Requirement: Evidence Before Acknowledgement

A connector SHALL commit a durable, connector-owned evidence row for an event before it
acknowledges that event, advances its checkpoint or cursor, or submits it to Switchboard.
`switchboard.message_inbox` is a routing buffer, never an evidence surface: no connector
SHALL rely on it as the verbatim copy of what arrived. A source with no evidence table of its
own SHALL write `connectors.evidence_journal`; a source with a dedicated evidence table
(`connectors.owntracks_points`, the Spotify and Steam tables) keeps that table as its surface.
A row in `connectors.evidence_journal` SHALL hold no more than the envelope's
`message_inbox.raw_payload` would hold at the envelope's ingestion tier, after bearer
scrubbing. That ceiling applies to the generic journal only: a dedicated evidence table keeps
its own documented retention (for OwnTracks, `connectors.owntracks_points.raw_payload` and the
Chronicler location-retention contract in `docs/connectors/owntracks.md`). A tier-3 or
policy-blocked event that is never submitted SHALL write no row, and the filtered row it
leaves SHALL NOT be replayable as evidence. An explicit owner replay bypasses filter
re-evaluation (the Replay Queue Drain Loop requirement): the policy gate applies to first
delivery only, and evidence validation applies to both. Before a replay submits, the stored
envelope SHALL be verified: it SHALL carry its original idempotency key, and evidence for it
SHALL already exist or be rebuildable from the item's own dedicated table. A replay SHALL NOT
create a broader copy of a row that a dedicated table already holds. The one exception: OwnTracks
transition and waypoint replay MAY commit the stored envelope to the generic journal
idempotently before it submits, at the envelope's own tier, because a legacy row parked before
evidence-first has no other evidence; every other replay writes none. Anything that fails the
check is not replayable: it sends nothing and writes no evidence.

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

#### Scenario: Every Spotify envelope takes one submit path

- **WHEN** the Spotify connector first sends a closed session, spoken start, context start, listening digest or gap-fill digest
- **THEN** it SHALL pass the ingestion-policy gate, write that envelope's evidence, and only then submit
- **AND** a failed write, or a failed submit whose park is not durable, SHALL retain the item for the next poll
- **AND** when the retained items reach a fixed cap the connector SHALL stop consuming new one-shot events (pause its playback poll), report health `degraded` with reason `held_backpressure`, and keep retrying; it SHALL NOT evict a retained item
- **AND** an event the source shows only while polling is paused is a source limit, not an event the connector consumed and skipped
- **AND** a digest's track-play rows SHALL be written in one transaction after the digest is delivered, so a failed write holds the cursor and the retry rebuilds the same digest with the same idempotency key

#### Scenario: A replay verifies evidence and never re-runs policy

- **WHEN** an owner replays a parked Spotify, Google Health or OwnTracks item
- **THEN** the connector SHALL NOT re-evaluate policy, and SHALL submit only after confirming the item's committed evidence (a dedicated row or a journal row, found by idempotency key; a replay writes none)
- **AND** a preview with no original idempotency key, or an OwnTracks location whose `owntracks_points` row is missing, SHALL be refused with nothing sent

#### Scenario: A legacy OwnTracks transition or waypoint replay journals its stored envelope

- **WHEN** an owner replays an OwnTracks transition or waypoint row parked before evidence-first, which has no journal row
- **THEN** the connector MAY commit the stored envelope to `connectors.evidence_journal` idempotently, at the envelope's own tier, before it submits
- **AND** the write SHALL be a no-op when the row exists and SHALL NOT widen the envelope; a location replay and every other connector's replay write nothing

#### Scenario: Duplicates collapse and revisions append

- **WHEN** the same `(source, endpoint_identity, provider_id, provider_revision)` is delivered again
- **THEN** exactly one journal row SHALL exist for it
- **AND** a new `provider_revision` SHALL append a second row

#### Scenario: The journal is append-only

- **WHEN** a statement updates, deletes or truncates `connectors.evidence_journal`
- **THEN** the database SHALL refuse it until a per-source retention contract provides a sanctioned deleter
