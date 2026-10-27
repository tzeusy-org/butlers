## ADDED Requirements

### Requirement: Owner knowledge gaps

A memory-enabled butler SHALL record an unanswered owner question as a typed knowledge gap in its own
memory schema: `entity_id`, `predicate`, `question_summary`, `asked_at`, `expires_at` (default
ninety days after `asked_at`), one origin row per asking thread (`origin_conversation_id`,
`origin_request_id`, `origin_channel`), and a `status` of `open`, `answerable`, `delivered`,
`dismissed` or `expired`. At most one `open` gap SHALL exist per `(entity_id, predicate)`. Gap
capture SHALL validate that the entity exists and is not merged away and that the predicate is
registered, and SHALL NOT read or write any other butler's schema.

A write of an active fact for the same `(entity_id, predicate)` SHALL move the open gap to
`answerable` in the same transaction as the fact write, recording the fact reference, a bounded
value excerpt and the fact's server-stamped authority. A gap whose matching fact already exists
when it is captured SHALL be born `answerable`. Capture and closure SHALL serialize so that a
fact write racing a gap insert cannot leave the gap `open`.

A deterministic module-default job (`memory_knowledge_gap_delivery`) SHALL post exactly one notice per origin thread of each
`answerable` gap, then mark the gap `delivered`. A fact whose authority is owner-class or `system`
SHALL read "now known"; a fact of any other authority SHALL read "reported by" the sender and
SHALL NOT be presented as established. A failed delivery SHALL keep the gap `answerable`, count the
attempt and retry with bounded backoff; once attempts are exhausted the gap SHALL stay visible in
the open-gap list with its last error. The same job SHALL mark `open` gaps past `expires_at` as
`expired` without notice. `dismissed` and `expired` are terminal.

The owner SHALL be able to list gaps through the `memory_open_gaps` tool and `GET /api/memory/gaps`
(cursor pagination, degraded envelope), each with a coverage state composed from the existing
`present`, `absent_proven`, `unknown` or `unavailable` vocabulary; missing coverage SHALL read
`unknown`.

#### Scenario: A decline with a gap records exactly one open row

- **WHEN** a dashboard-routed butler calls `conversation_reply` without `sources` and with a `gap` naming a live entity and a registered predicate
- **THEN** exactly one `open` gap SHALL exist for that pair with the origin request id taken from routing context
- **AND** an invalid entity or predicate SHALL fail the gap write without blocking the decline reply

#### Scenario: A duplicate decline merges

- **WHEN** a second decline names the same `(entity_id, predicate)` from another conversation
- **THEN** the single open gap SHALL list both origins and SHALL receive one notice per origin

#### Scenario: A fact write answers the gap in its own transaction

- **WHEN** `store_fact` stores an active fact for an open gap's `(entity_id, predicate)`
- **THEN** the gap SHALL become `answerable` in the same transaction with the fact's `memory_ref`
- **AND** a replay of the same fact write SHALL NOT notify again

#### Scenario: A fact that predates the gap yields an answerable gap

- **WHEN** a gap is captured for a pair that already has an active fact
- **THEN** the gap SHALL be born `answerable`

#### Scenario: A third-party fact is reported, not established

- **WHEN** the closing fact carries `third_party` authority
- **THEN** the notice SHALL read "reported by" the sender entity and SHALL NOT use "now known"

#### Scenario: Expiry and dismissal are terminal

- **WHEN** an `open` gap passes `expires_at` or the owner dismisses it
- **THEN** it SHALL become `expired` or `dismissed`, SHALL send no notice, and SHALL NOT reopen on a later fact write

#### Scenario: Delivery failure never drops a gap silently

- **WHEN** posting the notice fails
- **THEN** the gap SHALL stay `answerable` with its attempt count and last error and SHALL be retried until attempts are exhausted
