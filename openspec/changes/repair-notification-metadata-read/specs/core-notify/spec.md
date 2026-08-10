## ADDED Requirements

### Requirement: Notification Metadata Legacy Read Normalization
Every `NotificationSummary` emitted by `GET /api/notifications`, `GET /api/butlers/{name}/notifications`, and `PATCH /api/notifications/{id}/read` SHALL expose `metadata` as an object or `null` through one shared, one-layer normalizer. A mapping SHALL be returned as a shallow object copy, `null` SHALL remain `null`, and a legacy JSONB string whose single JSON parse yields an object SHALL return that object. A malformed string, a string whose single parse cannot complete because of a JSON decoder safety limit, or a string whose single parse yields a non-object, SHALL return `{"_raw": <the original outer string>}`. An actual non-string, non-object JSONB value SHALL return `null`. The normalizer SHALL NOT recursively decode, infer provenance, or change any delivery or status field.

ID: REQ-core-notify-001
Source: heart-and-soul/vision.md #4; RFC 0007 Response Envelope; design.md D1-D2
Scope: v1-mandatory

#### Scenario: Every response path preserves mappings, nulls, and encoded objects

- **WHEN** each of the global list, butler-scoped list, and mark-read response
  returns rows whose decoded metadata is respectively a mapping, `null`, and a
  string encoding one JSON object
- **THEN** each response returns respectively a shallow object copy, `null`,
  and the decoded object under `metadata`
- **AND** every other response field retains its existing value and semantics

#### Scenario: Every response path retains malformed, decoder-limited, and inner non-object strings

- **WHEN** each notification response path returns a legacy metadata string
  that is malformed JSON, cannot complete a one-layer parse because of a JSON
  decoder safety limit, or yields an array, string, number, boolean, or `null`
- **THEN** its `metadata` value is exactly `{"_raw": <the original outer
  string>}`
- **AND** the response does not fail serialization or silently return `null`

#### Scenario: Actual non-string non-object metadata remains null

- **WHEN** any notification response path returns an actual JSONB array,
  number, or boolean rather than a JSONB string scalar
- **THEN** it returns `metadata: null`
- **AND** it does not wrap that non-string value in `_raw`

#### Scenario: Encoded values are not recursively decoded or interpreted

- **WHEN** a legacy metadata string parses once to another string that itself
  resembles JSON text
- **THEN** the response returns `{"_raw": <the original outer string>}`
- **AND** it does not perform a second parse or infer missing provenance

### Requirement: Serving Writer Evidence Gates Historical Repair
Historical notification metadata repair SHALL NOT begin until read-only deployment evidence proves that every active process able to write `switchboard.notifications` serves writer fix #3458 (`7d2bea3bc`) or a descendant. The evidence SHALL identify each actual process/container, its image digest or runtime revision, command, and any bind-mounted source; verify the active Switchboard migration frontier; record only aggregate candidate-band bounds and counts; and cover a bounded post-deploy observation window with no new JSONB string metadata rows and no growth in the historical candidate band. A checkout, merge SHA, branch name, or image tag alone SHALL NOT satisfy this gate.

ID: REQ-core-notify-002
Source: heart-and-soul/vision.md #4; RFC 0008 Container Environment Isolation; design.md D4
Scope: v1-mandatory

#### Scenario: Complete serving-writer evidence authorizes the repair step

- **WHEN** the evidence identifies every active writer process and each is
  proven to serve #3458 or a descendant
- **AND** the migration frontier, aggregate candidate bounds, and clean bounded
  observation window are recorded without raw notification content
- **THEN** the historical-repair migration may proceed through the normal
  deployment workflow

#### Scenario: Incomplete or stale serving-writer evidence blocks repair

- **WHEN** any writer process, image/runtime revision, bind source, or
  migration-frontier proof is missing or does not meet the fixed-writer
  condition
- **THEN** the workflow records an external deployment blocker
- **AND** it does not execute a historical repair, manual SQL update, or
  workaround mutation

#### Scenario: New string-shaped metadata during observation blocks repair

- **WHEN** a post-deploy observation window finds a new JSONB string metadata
  row or growth in the historical candidate band
- **THEN** the writer-evidence gate fails closed
- **AND** the workflow reports the evidence gap without exposing raw metadata
  or executing the repair

### Requirement: Bounded Switchboard Historical Metadata Repair
After the serving-writer evidence gate succeeds, the next Switchboard migration SHALL capture one repair-start cutoff and atomically repair only `notifications` rows created before that cutoff whose metadata is a JSONB string. A valid one-layer encoded object SHALL become an object; a malformed or one-layer non-object string SHALL become `{"_raw": <the original string>}`. To prevent malformed inner JSON from aborting that atomic repair, the migration SHALL use a session-local exception-safe parser that attempts exactly one parse of the outer string and catches only `invalid_text_representation` (SQLSTATE `22P02`); a parse failure and every parsed non-object SHALL follow the `_raw` path, with no recursive parse. The migration SHALL leave all non-string metadata values and all other columns untouched, use an absent-relation guard that succeeds as an aggregate no-op when the Switchboard relation is absent, emit aggregate-only repair evidence, and retain an intentional data no-op downgrade.

ID: REQ-core-notify-003
Source: RFC 0006 Database Schema and Isolation; [Observed] roster/switchboard/migrations/001_switchboard_messaging.py; design.md D3
Scope: v1-mandatory

#### Scenario: Pre-cutoff string rows are repaired in one transaction

- **WHEN** the guarded Switchboard migration finds pre-cutoff JSONB string
  metadata rows after the serving-writer gate has succeeded
- **THEN** one atomic set-based repair converts valid encoded objects and stores
  malformed or non-object strings under `_raw`
- **AND** a migration failure leaves the candidate rows unchanged because the
  transaction rolls back

#### Scenario: Malformed inner JSON cannot abort the atomic repair

- **WHEN** a migration regression seeds pre-cutoff JSONB string candidates with
  both a valid encoded object and malformed inner text such as `{"broken":`
- **THEN** the exception-safe one-layer parser lets the one set-based repair
  complete: the valid candidate becomes an object and the malformed candidate's
  `_raw` member equals the exact original outer text `{"broken":`
- **AND** the parser does not recursively decode either candidate or swallow an
  error outside the malformed-input parse case

#### Scenario: The repair excludes post-cutoff and non-string rows

- **WHEN** the migration encounters a string row created at or after its
  captured cutoff, or a pre-cutoff mapping, `null`, array, number, or boolean
- **THEN** that row's metadata and every unrelated column remain unchanged
- **AND** rerunning the completed migration does not re-encode repaired values

#### Scenario: An absent Switchboard relation is a safe no-op

- **WHEN** the Switchboard migration runs in a database where the target
  `notifications` relation is absent
- **THEN** it succeeds without creating or mutating the relation
- **AND** it emits only aggregate no-op evidence

#### Scenario: Repair evidence never leaks notification payloads

- **WHEN** the migration records its cutoff, candidate bounds, converted count,
  or `_raw` fallback count
- **THEN** the evidence contains aggregate values only
- **AND** it contains no raw metadata, message body, recipient, or row
  identifier

#### Scenario: Downgrade does not recreate legacy strings

- **WHEN** the Switchboard migration is downgraded after a successful repair
- **THEN** its data operation is a deliberate no-op
- **AND** it does not re-encode objects, remove `_raw`, or mutate repaired
  notification rows
