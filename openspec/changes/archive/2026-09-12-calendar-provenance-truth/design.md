## Context

The calendar module parses Google date-only payloads into midnight datetimes,
but its provider projection currently writes `all_day=False`. The context
producer then queries only `status`, `all_day`, and time bounds, while the
radar builds candidates without event metadata. As a result, date-only events
and explicit butler-generated provider events can be interpreted as human
meetings.

The initial reversible-mutation implementation captures `all_day` in a
pre-image, but the dashboard inverse builders and update PATCH model omit it.
Undo can therefore send all-day midnight values back to Google as `dateTime`
boundaries and alter provider event semantics.

The completed `context-bus-producers` change owns the existence, writer, and
TTL semantics of the general calendar producer. This change narrows only the
truthfulness of the producer and radar inputs. The workspace remains the
faithful provider projection required by `module-calendar`.

## Goals / Non-Goals

**Goals:**

- Carry Google date-only provenance through parser and projection as
  `all_day=true`.
- Preserve nullable all-day truth through undo so date-only events remain
  date-only across update and delete inverses.
- Give context and radar one deterministic, testable eligibility rule for
  explicit generated metadata and legacy all-day-shaped rows.
- Preserve timed human event behavior when provenance is absent or malformed.
- Remove exactly the three known obsolete source keys and reject invalid roster
  names at internal source registration.
- Retain existing projection-table fail-open behavior and source/event cascade
  semantics.

**Non-Goals:**

- Do not hide or delete butler-authored provider rows from the workspace.
- Do not infer authorship from title prefixes, source names, or calendar lanes.
- Do not change Google provider authority, conflict thresholds, producer
  scheduling, context TTLs, or database schema.
- Do not introduce a general source-name blacklist or clean unrelated ledger
  rows.

## Decisions

### D1 — Store exact provider all-day truth; apply the legacy heuristic only at analysis time

`CalendarEvent` gains an explicit `all_day` field. The Google parser marks it
true only when both provider boundaries are date-only, and
`_project_provider_changes` forwards that value to the existing event upsert.
This preserves a true provider fact rather than guessing from duration.

The same fact is part of reversible action pre-state. The dashboard inverse
builders pass it unchanged to `calendar_update_event` and
`calendar_create_event`; the update model and Google PATCH serializer use it
to emit `start.date`/`end.date` rather than `dateTime`. This correction leaves
the existing `this`, `following`, and `series` recurrence-instance behavior
unchanged.

The >=24-hour local-midnight rule is a defensive read-time analysis heuristic,
not a projection rewrite. It protects old rows without reclassifying legitimate
long timed provider events in storage.

**Alternative considered:** mark every >=24-hour event all-day during sync.
Rejected because duration alone does not prove an event is all-day and would
alter provider-authoritative projection state.

### D2 — Use one pure provenance/eligibility helper for context and radar

A small deterministic temporal helper will accept raw metadata, bounds,
timezone, and `all_day`. It recognizes generated provenance only from the
explicit `butler_generated=true` marker and recognizes legacy all-day-shaped
rows only after successful IANA timezone conversion. It will return no
generated/legacy exclusion for malformed metadata or timezone values, allowing
otherwise valid timed human rows to retain existing behavior without a raised
error.

The context producer will fetch metadata and timing for active candidates,
choose the latest eligible row, and clear its signals only when none remains.
The radar will apply the same filter before constructing
`ConflictCandidate`s, so the single filter governs overlap, back-to-back, and
overloaded-day detection.

**Alternative considered:** SQL-only JSON/timezone predicates. Rejected because
legacy timezone handling and malformed JSON would become database-specific,
harder to test, and would risk turning a parse error into a query failure.

### D3 — Preserve workspace visibility while separating analysis eligibility

The projection writer continues to upsert every provider event and retain its
metadata. Context and radar are analytical interpretations, not projection
authority. Filtering happens after workspace rows are fetched, leaving the grid,
search, provider reconciliation, and event/entity cascade unchanged.

**Alternative considered:** exclude generated events from provider projection.
Rejected because it contradicts the existing dual-lane projection contract and
would hide events the owner needs to see.

### D4 — Use exact-key ledger cleanup and roster-derived internal registration validation

The module will keep a fixed immutable set of the three obsolete source keys
and delete with an exact `source_key = ANY(...)` predicate. It will call a
small roster-name validator only for internal scheduler/reminder registration;
provider sources retain their existing registration path. A rejected name
returns without an insert. Valid names continue through the existing idempotent
upsert.

**Alternative considered:** a pattern match for `internal_*:butler*`.
Rejected because it would delete or reject future valid source names and would
violate the bounded cleanup contract.

## Risks / Trade-offs

- [Old metadata is non-object JSON] → Treat it as no explicit generated marker;
  no parse error can silently suppress a human event.
- [Timezone is malformed] → Skip only the legacy-midnight inference; retain the
  timed event for normal analysis rather than creating a false all-day state.
- [An active excluded event precedes an eligible event] → Query ordered active
  rows and choose the first eligible one, not simply `LIMIT 1` before filtering.
- [Roster lookup fails unexpectedly] → Reject internal registration rather than
  persist an unverified source; normal projection-table-unavailable behavior
  remains fail-open/no-write.
- [Source cleanup cascades] → Exact-key delete intentionally retains database
  cascade behavior, while focused tests protect valid source rows.
- [All-day pre-state contains midnight datetimes] → The explicit `all_day`
  truth selects Google date-only serialization, so undo does not reinterpret
  those boundaries as timed values.

## Migration Plan

1. Deploy the code and focused tests without a schema migration.
2. On each calendar module startup, execute the idempotent exact-key cleanup;
   repeated starts become a no-op.
3. Existing legacy events retain stored values but are protected by read-time
   eligibility until a future provider sync supplies definitive all-day data.
4. Rollback is code-only: projection rows remain intact, and the cleanup does
   not require reconstruction beyond the normal source registration path.

## Open Questions

None. The task supplies the bounded source-key set and the authoritative
provenance marker; the existing `context-bus-producers` change remains the
authority for producer lifecycle semantics.
