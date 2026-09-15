## ADDED Requirements

### Requirement: Runtime Session Correlation for notify() Ledger Rows

Every `public.attention_ledger` row written at the `notify()` boundary with `source="notify"` SHALL carry the runtime session id of the session that made the call, under the `session_id` key of the row's `metadata`, when a runtime session id is bound.

The session id SHALL be read once at the top of the `notify()` call, so every terminal outcome the same call can reach names the same session. Recording it SHALL remain best-effort and fail-open like the rest of the ledger write: an unbound session id SHALL leave the metadata otherwise unchanged rather than failing the notification.

This exists so that a caller holding a session id (for example, the id returned by a `trigger` result) can ask the notification path what became of that session's notification, instead of guessing from the originating butler and a time window and risking crediting an unrelated notification.

A reader SHALL be provided that returns the terminal notify dispatch recorded for one `(origin_butler, session_id)` pair, preferring a `delivered` row over any other outcome and the most recent row within an outcome, and returning nothing when the ledger holds no such row.

That reader SHALL NOT fail open. A ledger that cannot be read SHALL surface as an error to its caller rather than as an empty result, because "no row" and "could not look" are different answers and a caller may only claim absence of evidence for the former.

#### Scenario: A notify ledger row names its runtime session

- **WHEN** `notify()` is called inside a runtime session and reaches any terminal outcome
- **THEN** the `public.attention_ledger` row it writes SHALL carry that runtime session id under `metadata.session_id`
- **AND** any metadata the call site already supplied SHALL be preserved alongside it

#### Scenario: An unbound session leaves the row unchanged

- **WHEN** `notify()` is called with no runtime session id bound
- **THEN** the ledger row SHALL be written exactly as it would be without this requirement, with no `session_id` key

#### Scenario: The reader returns the dispatch recorded for a session

- **WHEN** the ledger holds a `source="notify"` row for a given origin butler and session id
- **THEN** the reader SHALL return that row's `outcome`, `occurred_at`, `channel`, `reason` and `notification_ref`

#### Scenario: The reader distinguishes no row from no answer

- **WHEN** the ledger holds no `source="notify"` row for that origin butler and session id
- **THEN** the reader SHALL return nothing
- **AND WHEN** the ledger cannot be read at all
- **THEN** the reader SHALL raise rather than return nothing, so a caller cannot mistake an unreadable ledger for a confirmed absence
