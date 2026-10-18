# Connector Event Clock

## ADDED Requirements

### Requirement: Source event time is a separate, nullable, provenance-tagged field

`ingest.v1` `event` SHALL accept three optional fields that describe when the event happened at the
source, distinct from `observed_at` (when the connector observed it) and from the Switchboard's
receipt time: `occurred_at`, `occurred_at_basis`, and `occurred_at_precision`.

`occurred_at_basis` SHALL be a closed vocabulary of exactly `source_stated` (the provider's payload
states the time), `connector_derived` (the connector computed it from source data that is not itself
a stated time, for example by decoding an identifier), and `receipt_only` (the connector has no
source time). `occurred_at_precision` SHALL be exactly `instant` or `day`. `occurred_at` SHALL be a
timezone-aware RFC 3339 string when present and `null` otherwise.

`occurred_at` SHALL be `null` if and only if the basis is `receipt_only`. Absence of a source time
SHALL stay absence: a connector SHALL NOT substitute its own clock, a poll time, or the receipt time
for `occurred_at`. An envelope that carries none of the three fields SHALL be read as `receipt_only`.
A source time whose UTC offset the provider does not state SHALL NOT be labelled `source_stated`
at `instant` precision unless the provider documents it as UTC.

The existing `observed_at` field and its meaning SHALL NOT change.

ID: REQ-connector-event-clock-001
Source: bu-h40h2b.12 Slice A (event identity and two clocks); [Observed] roster/switchboard/tools/routing/contracts.py IngestEventV1 has `observed_at` and no source-time field
Scope: v1-mandatory

#### Scenario: Source-stated time

- **WHEN** a connector submits an event whose provider payload states its time
- **THEN** the envelope carries `occurred_at` as a timezone-aware RFC 3339 string, `occurred_at_basis` `source_stated` and an `occurred_at_precision`

#### Scenario: No source time stays absent

- **WHEN** a connector has no source-stated or derivable time for an event
- **THEN** `occurred_at` is `null` and `occurred_at_basis` is `receipt_only`
- **AND** the connector's poll time is not placed in `occurred_at`

#### Scenario: Invalid combinations are rejected at the ingest boundary

- **WHEN** an envelope carries an unknown basis or precision value, an `occurred_at` without a basis, a `receipt_only` basis with an `occurred_at`, a non-`receipt_only` basis without an `occurred_at`, or an `occurred_at` that is not a timezone-aware RFC 3339 string
- **THEN** the Switchboard rejects the envelope with a validation error
- **AND** nothing is persisted for it

#### Scenario: Legacy envelope

- **WHEN** a connector that has not adopted these fields submits an envelope
- **THEN** it is accepted unchanged and read as `receipt_only`

#### Scenario: Offset-less source time

- **WHEN** a provider gives a wall-clock time with no UTC offset and does not document it as UTC
- **THEN** the connector uses `receipt_only`, or `day` precision when only the calendar date is trustworthy
- **AND** it does not label the value `source_stated` at `instant` precision

### Requirement: The receipt clock is independent and never rewritten

The Switchboard SHALL keep stamping `received_at` itself when it accepts an envelope. `received_at`
SHALL NOT be taken from, replaced by, or recomputed from `occurred_at` or `observed_at`. Events whose
source time is old, including events a connector replays from history, SHALL keep the receipt time at
which they were accepted. No historical ingestion record SHALL be rewritten by this contract, and
records written before it SHALL keep a null source time and a `receipt_only` reading.

ID: REQ-connector-event-clock-002
Source: bu-h40h2b.12 Slice A; [Observed] roster/switchboard/tools/ingestion/ingest.py stamps `received_at = datetime.now(UTC)` and writes it to `message_inbox` and `public.ingestion_events`
Scope: v1-mandatory

#### Scenario: Replayed old event keeps its receipt time

- **WHEN** a connector replays an email from a year ago with a `source_stated` `occurred_at`
- **THEN** its `received_at` is the time the Switchboard accepted it
- **AND** its `occurred_at` is the year-old source time

#### Scenario: Existing rows are untouched

- **WHEN** this contract is adopted
- **THEN** no existing `public.ingestion_events` row gains a source time
- **AND** each reads as `receipt_only`

### Requirement: Source time is persisted beside the receipt time

The Switchboard SHALL persist `occurred_at`, `occurred_at_basis` and `occurred_at_precision` as
discrete fields of the ingestion event record next to `received_at`, so that readers can filter and
order on them. Persistence only inside the raw payload SHALL NOT satisfy this requirement. Event
identity and deduplication SHALL NOT use any of the three fields: the provider event identifier and
idempotency key stay the identity, and the hourly content-hash bucket keeps using `observed_at`.

ID: REQ-connector-event-clock-003
Source: bu-h40h2b.12 Slice A; [Observed] `observed_at` is persisted only inside `message_inbox.raw_payload` and the `public.ingestion_events` column list has no `observed_at`; `_compute_content_hash_key` buckets on `event.observed_at`
Scope: v1-mandatory

#### Scenario: Discrete fields

- **WHEN** an envelope with a `source_stated` time is accepted
- **THEN** the ingestion event record holds the time, basis and precision in their own fields
- **AND** a query can filter on them without parsing a payload

#### Scenario: Identity ignores the source time

- **WHEN** a connector retries an event with the same provider event identifier but a different derived `occurred_at`
- **THEN** it is the same event and no second record is created

#### Scenario: Dedupe bucket is unchanged

- **WHEN** the content-hash dedupe key is computed
- **THEN** its time bucket still comes from `observed_at`

### Requirement: Duplicate, late and corrected events preserve identity and provenance

A redelivery of an already-accepted event SHALL keep the stored `occurred_at`, basis, precision and
receipt time; a later redelivery SHALL NOT overwrite them, including when it carries a better basis.
A late event, one whose source time is earlier than events already accepted for the same source,
SHALL be accepted and SHALL NOT be dropped, reordered in receipt chronology, or rejected for being
stale on source time. A provider-reported correction or edit SHALL be a new event with its own
provider identity and its own source time, and SHALL NOT overwrite the original event's fields.

ID: REQ-connector-event-clock-004
Source: bu-h40h2b.12 Slice A acceptance 1 and 2; [Observed] Telegram edits, Calendar `updated` and Drive `modifiedTime` already enter the idempotency key (src/butlers/connectors/google_calendar.py, google_drive.py)
Scope: v1-mandatory

#### Scenario: Redelivery does not rewrite

- **WHEN** an event is redelivered with a different `occurred_at` or a better basis
- **THEN** the stored fields and `received_at` are unchanged

#### Scenario: Late event

- **WHEN** an event arrives whose `occurred_at` precedes events already accepted from the same source
- **THEN** it is accepted with its own `received_at`
- **AND** earlier receipt records keep their order

#### Scenario: Correction is a new event

- **WHEN** a provider reports an edit of an earlier event
- **THEN** the edit arrives under its own provider identity with its own source time
- **AND** the original event's fields are unchanged

### Requirement: Time-ordered reads name their basis and keep receipt chronology visible

Any read surface that orders or filters ingestion events by time SHALL state which basis it uses.
The default SHALL remain the receipt basis. A surface MAY offer an occurred-time presentation that
uses `occurred_at` and falls back to `received_at` when it is null, and SHALL then expose, for each
row, the basis that was used, the event's `occurred_at_basis`, and the `received_at`. A surface
SHALL NOT mix bases in one ordering without labelling, SHALL NOT present a `receipt_only` row as
though it had a source time, and SHALL give every ordering a deterministic total order by adding the
event identifier as the final tie-breaker. An occurred-basis presentation SHALL NOT change the
receipt-basis ordering or the pagination of any receipt-basis read.

ID: REQ-connector-event-clock-005
Source: bu-h40h2b.12 Slice A and API/UI; [Observed] src/butlers/api/routers/ingestion_events.py keyset-paginates on `(received_at DESC, id DESC)` and filters on `received_at`
Scope: v1-mandatory

#### Scenario: Default stays receipt basis

- **WHEN** a caller requests the ingestion event list without selecting a basis
- **THEN** rows are ordered and paginated on `(received_at DESC, id DESC)` as today

#### Scenario: Occurred presentation falls back and says so

- **WHEN** a caller selects the occurred basis and a row has no `occurred_at`
- **THEN** the row is placed by `received_at`
- **AND** the row reports that it used the receipt basis and that its `occurred_at_basis` is `receipt_only`

#### Scenario: Provenance is visible

- **WHEN** a row has a `connector_derived` time
- **THEN** the row reports `connector_derived`, not `source_stated`

### Requirement: Each connector adopts the source time independently and without new provider calls

A connector SHALL adopt `occurred_at` on its own schedule. Adoption SHALL use only data the
connector already has in hand when it builds the envelope. It SHALL NOT add a provider call, change
an OAuth scope, request additional fields, or replay history in order to learn a source time. A
connector that does not adopt remains `receipt_only`. Adoption by one connector SHALL NOT require or
imply adoption by another.

ID: REQ-connector-event-clock-006
Source: bu-q21jjn posture A (model existing data, no provider calls); bu-bcgpnd closed 2026-10-17 as an engineering call
Scope: v1-mandatory

#### Scenario: No new provider traffic

- **WHEN** a connector adopts the source time
- **THEN** its provider call set, requested fields and OAuth scopes are unchanged

#### Scenario: Partial adoption

- **WHEN** Gmail adopts and Discord does not
- **THEN** Gmail events carry a source time and Discord events read as `receipt_only`

### Requirement: No backfill door is offered by the event clock or the coverage surface

This contract SHALL NOT add, extend, link to, or trigger a backfill. No coverage or connector-detail
surface defined here SHALL start a provider call, replay a cursor, or create a backfill job, on
page load or on any other path. If a surface chooses to show a backfill affordance, it SHALL be
disabled and SHALL show the fixed reason code `backfill_not_authorized` with the text "Backfill is
not available here: no owner-authorized backfill path exists for this source." Existing connector
backfill and replay paths, and the Switchboard backfill controls, are neither changed nor made
reachable from these surfaces. An owner-triggered backfill SHALL require its own owner decision and
its own change.

ID: REQ-connector-event-clock-007
Source: bu-h40h2b.12 Slice C (backfill request door, separately authorized); bu-bcgpnd closed 2026-10-17 (an owner-triggered backfill path stays out); [Observed] existing paths: Gmail `backfill.poll`, WhatsApp bridge `/backfill`, Telegram user client `CONNECTOR_BACKFILL_WINDOW_H`
Scope: v1-mandatory

#### Scenario: No affordance by default

- **WHEN** a coverage or connector-detail view is rendered for a source with a gap
- **THEN** it offers no control that starts a backfill

#### Scenario: Disabled affordance states the reason

- **WHEN** a view renders a backfill affordance anyway
- **THEN** it is disabled
- **AND** it shows reason code `backfill_not_authorized` and the fixed text

#### Scenario: Nothing runs on load

- **WHEN** any surface defined by this change loads
- **THEN** no provider call, cursor replay or backfill job is started

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure) and Rule 7 (transport is connector responsibility)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0038 (goal runtime; ingest envelopes, dedupe and the durable buffer remain and feed goal admission)
