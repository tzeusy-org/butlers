## Why

Three owner-visible facts per connector are not available today: when things happened, what span of
the source the system holds, and whether a sense is alive. The dashboard shows cursors and receipt
times as if they were coverage (bu-h40h2b.12, waiting since 2026-09-14).

- **One clock.** `ingest.v1` carries `event.observed_at`, which the base spec defines as "when the
  connector observed the event". Connectors use it two ways: Gmail and Home Assistant (WebSocket
  `time_fired`; the REST poll puts poll time) put a source-stated time in it, while Telegram (bot and user client), Discord, Steam,
  Spotify, OwnTracks, ActivityWatch, Google Calendar, Google Drive and Google Health put in their
  own poll or receive time; WhatsApp does both, and the live listener
  stamps a monotonic-clock reading as if it were an epoch time, so its envelopes are dated 1970
  plus host uptime (bu-0wearu; design.md survey). The Switchboard stamps its own
  `received_at`, persists it on `public.ingestion_events`, and every dashboard read orders on it.
  `observed_at` is persisted only inside `message_inbox.raw_payload`. A replayed year of email
  therefore lands "today" with no source time anywhere a reader can query.
- **Cursor theater.** Connectors persist a cursor per `(connector_type, endpoint_identity)`, but most
  are opaque tokens (Gmail `historyId`, Telegram update offset, Drive page token, Calendar
  `syncToken`). None says which span of the source was covered, and a gap left by an outage or a
  full re-sync is not recorded.
- **No silent-source signal.** Expected signals already know that an absent signal needs a live
  producer, and fleet cases already hold a situation by correlation key, but nothing opens a case
  when a live source stops producing events.

## What Changes

### Added Capabilities

- `connector-event-clock` (slice A, plus the slice C constraint): optional `event.occurred_at`,
  `event.occurred_at_basis` (closed: `source_stated`, `connector_derived`, `receipt_only`) and
  `event.occurred_at_precision` (closed: `instant`, `day`); absence stays absence; `received_at` is
  never rewritten; persisted as discrete fields; duplicates, late and corrected events keep identity
  and provenance; time-ordered reads name their basis; each connector adopts independently with no
  new provider calls; and the backfill door is absent, or disabled with the fixed reason
  `backfill_not_authorized`.
- `connector-source-coverage` (slice B): per-source coverage assertions from existing evidence only,
  a read model whose states are `covered`, `gap`, `unknown`, `unavailable`, idempotent merge, no
  silent repair, redaction and bounded retention. An opaque cursor never renders as coverage.
- `connector-silent-source` (slice D): a deterministic Switchboard evaluation over registered expected
  signals that opens one RFC 0032 fleet case per lapsed source under a private-safe correlation key,
  resolves it on recovery, and causes no notification, provider call or remediation.

### Modified Capabilities

None. The three capabilities are new so that this change does not edit the `ingest.v1 Envelope
Contract` block that `generalist-lead-focused-delegation` already modifies.

## Posture

[decision] Posture A, chosen by the owner-vetted engineering call that closed bu-bcgpnd on
2026-10-17: model existing data, make no provider calls, and keep the backfill door disabled with a
stated reason. Posture B (an owner-triggered backfill with provider calls and quota cost) and C
(defer) are not taken. Reversible: yes. This change introduces no new collection, provider call, OAuth
scope change, backfill job, migration or code.

## Ordering against bu-xmueeq

bu-xmueeq (P1, open) changes the same envelope contract: it populates `untrusted_spans` and adds the
feed-or-message channel declaration, specified in the `generalist-lead-focused-delegation` delta of
`REQ-connector-base-spec-004`. This change does not modify that requirement; it adds new capabilities
that name three optional `event` fields, so the two specs can be adopted in either order without a
text conflict. Implementation of slice A touches `IngestEventV1` in
`roster/switchboard/tools/routing/contracts.py` and the ingest path, as bu-xmueeq does, so it is
sequenced after bu-xmueeq's envelope change merges, or rebased on it. Slices B and D do not touch the
envelope and are not ordered against bu-xmueeq.

## Ownership boundaries

- The fleet-case container, its state machine and the situation dashboard stay with bu-h40h2b.8. This
  change only names a producer key and the evidence it contributes, and does not create a parallel
  case surface. Slice D is `v1-reserved` until bu-h40h2b.8's owner approves the key format.
- Existing backfill controls and connector backfill paths (Gmail `backfill.poll`, the WhatsApp bridge
  `/backfill`, the Telegram user client `CONNECTOR_BACKFILL_WINDOW_H`, first-run history in
  ActivityWatch and Google Health) are neither changed nor linked.
- Connector freshness, source event time, coverage and source-lapse cases keep separate authority: a
  cursor or a receipt time is not proof of coverage.

## Impact

Spec only. On adoption bu-h40h2b.12 unblocks. Implementation acceptance items 2 and 4 to 8 of
bu-h40h2b.12 (synthetic fixtures, real-Postgres tests, UI) remain for the implementation beads. If the
owner declines, bu-h40h2b.12 closes as declined.
