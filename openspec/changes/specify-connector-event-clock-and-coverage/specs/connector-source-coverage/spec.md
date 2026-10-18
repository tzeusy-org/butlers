# Connector Source Coverage

## ADDED Requirements

### Requirement: Coverage is asserted per source from existing evidence, never from a cursor alone

A coverage assertion SHALL name one source, the pair `(connector_type, endpoint_identity)`, a span
with an inclusive UTC start and an exclusive UTC end, a state of `covered` or `gap`, an
`evidence_kind` from a closed vocabulary, and a bounded evidence reference. The closed
`evidence_kind` vocabulary SHALL be exactly: `time_cursor` (the connector's persisted checkpoint is
itself a source time and the connector consumed events up to it), `cursor_reset` (a full re-sync or
cursor invalidation was recorded, which makes the span before it a `gap` unless other evidence
covers it), and `connector_offline` (the exact source's heartbeat liveness was `stale` or `offline`
for the span).

An opaque cursor or token (a Gmail `historyId`, a Telegram update offset, a Drive page token, a
Calendar `syncToken`, a Google Health record identifier, a WhatsApp event identifier) SHALL NOT by
itself produce a `covered` assertion or any time bound. A heartbeat showing the connector alive SHALL
NOT by itself produce `covered`. Assertions SHALL be built only from records the system already
holds; building one SHALL NOT cause a provider call.

ID: REQ-connector-source-coverage-001
Source: bu-h40h2b.12 Slice B (coverage assertions, not cursor theater); [Observed] src/butlers/connectors/cursor_store.py persists an opaque string per `(connector_type, endpoint_identity)`; time-valued cursors in home_assistant_checkpoint.py (`last_event_ts`), spotify.py (`after` ms), activitywatch.py (ISO timestamp), owntracks.py (`tst`)
Scope: v1-mandatory

#### Scenario: Time cursor produces a bounded span

- **WHEN** a connector whose cursor is a source time has consumed events up to that time
- **THEN** a `covered` assertion with `evidence_kind` `time_cursor` may bound a span that ends at that time

#### Scenario: Opaque cursor claims nothing about time

- **WHEN** a connector's only checkpoint is an opaque token
- **THEN** no `covered` assertion and no time bound is produced from that token
- **AND** the time before and around it reads `unknown`

#### Scenario: Liveness alone is not coverage

- **WHEN** a source's heartbeat is healthy and no cursor evidence exists
- **THEN** the span reads `unknown`, not `covered`

### Requirement: The read model returns known spans, explicit gaps, unknown and unavailable

A coverage read for a requested window SHALL return an ordered, non-overlapping list of segments
that together tile the whole window. Each segment SHALL carry exactly one state from `covered`,
`gap`, `unknown`, `unavailable`. Any part of the window with no assertion SHALL be `unknown`. If the
assertion store, the heartbeat history or the checkpoint read needed for the window cannot be read,
the affected segments SHALL be `unavailable`, and the response SHALL NOT be shaped as an empty
success. A response with zero assertions SHALL be a single `unknown` segment, never a `covered` one.
No surface SHALL render an all-clear from `unknown` or `unavailable` segments.

ID: REQ-connector-source-coverage-002
Source: bu-h40h2b.12 Slice B and acceptance 3; expected-signals "Expected-signal reads degrade honestly" (unavailable is not an empty result)
Scope: v1-mandatory

#### Scenario: Window is tiled

- **WHEN** a window overlaps one `covered` span and one `gap` span
- **THEN** the response lists segments for the covered span, the gap and the remaining time as `unknown`, in order, with no overlap and no hole

#### Scenario: Unreadable store is unavailable

- **WHEN** the read fails for a source
- **THEN** the segments for that source are `unavailable`
- **AND** the response is not an empty list of segments

#### Scenario: Cursor alone never renders complete

- **WHEN** a source has a cursor and no assertions
- **THEN** the response for that source is a single `unknown` segment

### Requirement: Assertions merge idempotently and gaps are never silently repaired

Recording the same assertion again (same source, `evidence_kind`, state and span) SHALL leave one
assertion. Overlapping or adjacent assertions of the same state and `evidence_kind` SHALL merge into
one span. Where a `covered` span and a `gap` span overlap, the overlap SHALL read as `gap`. A `gap`
SHALL be converted to `covered` only by a recorded repair, and this change defines no repair path
because it defines no backfill; until a later owner-authorized change adds one, a recorded gap
persists for the life of the assertion.

ID: REQ-connector-source-coverage-003
Source: bu-h40h2b.12 Slice B (merge/idempotence, repair history); REQ-connector-event-clock-007
Scope: v1-mandatory

#### Scenario: Repeat is a no-op

- **WHEN** the same assertion is recorded twice
- **THEN** one assertion exists

#### Scenario: Adjacent spans merge

- **WHEN** two `covered` `time_cursor` spans touch end to start
- **THEN** they read as one span

#### Scenario: Gap wins on overlap

- **WHEN** a `covered` span overlaps a `gap` span
- **THEN** the overlap reads as `gap`

### Requirement: Coverage records and responses are bounded, redacted and retention-limited

An assertion and a coverage response SHALL contain only the source pair, span bounds, state,
`evidence_kind`, a bounded evidence reference and timestamps. They SHALL NOT contain a raw provider
cursor or token, an event body or subject, a sender or recipient identifier, a credential or a
provider error text. Audit and telemetry output SHALL identify a source by `connector_type` and an
opaque endpoint reference, never by the endpoint identity string. An owner-facing view MAY show the
endpoint identity the connector-detail page already shows and SHALL NOT show more. Assertions SHALL
be retained for no longer than the heartbeat history and ingestion records they are derived from,
and SHALL be removed or reconciled when their source is removed.

ID: REQ-connector-source-coverage-004
Source: bu-h40h2b.12 API/UI, privacy, retention; [Observed] Gmail `endpoint_identity` is an email address
Scope: v1-mandatory

#### Scenario: No raw cursor leaves the store

- **WHEN** a coverage response or audit entry is produced
- **THEN** it contains no raw cursor, token, body, sender or credential

#### Scenario: Telemetry uses an opaque reference

- **WHEN** coverage is written to logs or metrics
- **THEN** the source appears as `connector_type` plus an opaque endpoint reference

#### Scenario: Source removal reconciles

- **WHEN** a source is removed from the connector registry
- **THEN** its assertions are removed or marked orphaned according to the documented reconciliation

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure)
- RFC 0029 (expected signals) and RFC 0032 (fleet case file), both under RFC 0038
- `expected-signals` capability, "Expected-signal reads degrade honestly"
