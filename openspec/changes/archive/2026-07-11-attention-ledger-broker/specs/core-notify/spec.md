# Core Notify — Attention Ledger + Context-Bus Gating

## ADDED Requirements

### Requirement: Attention Ledger Recording at the notify() Boundary
Every terminal decision the `notify()` owner-default quiet-hours gate makes SHALL be recorded to `public.attention_ledger` with a closed outcome vocabulary (`delivered`, `deferred`, `suppressed`) and a machine-readable `reason`. A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open).

#### Scenario: Quiet-hours suppression is recorded
- **WHEN** `notify()`'s owner-default path is suppressed by `public.approvals_policy` quiet hours
- **THEN** a `public.attention_ledger` row is written with `outcome="suppressed"` and `reason="quiet_hours"`
- **AND** the notify() call still returns `{"status": "suppressed_quiet_hours", ...}` to the caller unchanged

#### Scenario: Context-bus suppression is recorded
- **WHEN** `notify()`'s owner-default path is suppressed because an active `dnd` or `sleeping` context-bus signal is present (and quiet hours did not already suppress)
- **THEN** a `public.attention_ledger` row is written with `outcome="suppressed"` and `reason="context_bus:<signal_type>"`
- **AND** the notify() call returns `{"status": "suppressed_context_bus", "channel": ..., "context_signal": "<signal_type>"}`

#### Scenario: Delivery-preferences defer is recorded
- **WHEN** `notify()` defers a notification via the existing per-butler `delivery_preferences` quiet-hours mechanism
- **THEN** a `public.attention_ledger` row is written with `outcome="deferred"` and `reason="delivery_preferences_quiet_hours"`, and `notification_ref` set to the `deferred_notifications` row id

#### Scenario: Successful delivery is recorded
- **WHEN** `notify()` successfully delivers a notification (either via direct Switchboard self-delivery or via the switchboard client)
- **THEN** a `public.attention_ledger` row is written with `outcome="delivered"`, and `notification_ref` set to the delivery's `notification_id` when the delivery result provides one

#### Scenario: Ledger write failure never blocks delivery
- **WHEN** the `public.attention_ledger` table is unavailable (e.g. an unmigrated database) or the INSERT otherwise fails
- **THEN** `notify()` proceeds exactly as it would without this requirement — the ledger write is logged at WARNING and swallowed, never raised

### Requirement: Context-Bus Gating at the notify() Owner-Default Path
The `notify()` owner-default quiet-hours gate SHALL also consult the situational context bus (`public.user_context`, RFC 0009) for an active `dnd` or `sleeping` signal, deterministically (no LLM in the read path), before delivering. This check applies under the same scope as the existing quiet-hours gate: no explicit `entity_id`/`recipient`, intent in `{send, insight}`, and priority not `high`.

#### Scenario: Active dnd signal suppresses an owner-default send
- **WHEN** `notify(channel="telegram", message="...")` is called with no `entity_id`/`recipient`
- **AND** `public.user_context` has an active `dnd` signal (not expired, not superseded)
- **AND** approvals_policy quiet hours do NOT already suppress
- **THEN** the notification is suppressed with `{"status": "suppressed_context_bus", "context_signal": "dnd"}`

#### Scenario: Context-bus check is skipped when quiet hours already suppressed
- **WHEN** approvals_policy quiet hours already suppress the notification
- **THEN** the context-bus signal is not queried (avoids a redundant DB round-trip on an already-decided path)

#### Scenario: priority="high" bypasses both quiet hours and the context bus
- **WHEN** `notify(priority="high", ...)` is called during active quiet hours AND an active `dnd` signal
- **THEN** the notification is delivered immediately; neither gate suppresses it

### Requirement: Priority Normalization for Ledger Comparability
`notify()`'s 3-level `priority` enum (`high`/`medium`/`low`) SHALL be normalized onto the same 1-100 `priority_score` scale the insight pipeline uses (RFC 0011 Priority Scoring Convention), so `public.attention_ledger` rows from both boundaries are comparable. `"high"` MUST normalize to a score at or above `URGENT_PRIORITY_THRESHOLD` (90).

#### Scenario: high/medium/low map to comparable scores
- **WHEN** a ledger row is recorded for `notify(priority="high")`, `notify(priority="medium")`, and `notify(priority="low")`
- **THEN** the recorded `priority_score` values are 90, 50, and 20 respectively, and `priority_label` preserves the original string

### Requirement: Attention Ledger Reader
The dashboard API SHALL expose a windowed, filterable reader over `public.attention_ledger` and a per-source delivery-vs-suppression summary, so that a source silently failing at either choke point (`notify()` or `delivery_cycle()`) is observable instead of requiring direct DB access. This is slice 5 of this change (previously deferred — see RFC 0011 Amendment 1's Integration note).

`GET /api/attention/ledger` SHALL return a paginated, newest-first list of ledger rows, filterable by `intent`, `source` (the ledger's own `notify`/`insight` choke-point column), `outcome`, and `origin_butler`, and windowed by `since`/`until` (`occurred_at` bounds). `GET /api/attention/ledger/summary` SHALL return, for a `since`/`until` window (defaulting to the last 7 days when `since` is omitted), one row per distinct `origin_butler` with `delivered`/`coalesced`/`deferred`/`suppressed`/`total` counts and a `suppressed_never_delivered` boolean: `true` when that `origin_butler` has `suppressed > 0` and `delivered == 0` in the window. Both endpoints MUST follow the repo's degraded-envelope convention (`butlers/CLAUDE.md` API Conventions): a genuinely unreachable ledger pool renders `source_available=false` on an otherwise-empty/zero payload, never a truthful "no suppression" or "no rows".

Naming note: the summary's "per source" grouping is `origin_butler` (which butler/job attempted the egress — e.g. `secrets_lifecycle`, `home`), a distinct dimension from the ledger's own `source` column (the `notify`/`insight` choke-point literal). Both are independently exposed: `origin_butler` as the summary's grouping key and an optional list-endpoint filter, `source` as a list/summary filter on the choke-point column.

#### Scenario: Suppressed-but-never-delivered source is flagged
- **WHEN** `GET /api/attention/ledger/summary` is called for a window in which `origin_butler="secrets_lifecycle"` has 120 rows with `outcome="suppressed"` and 0 rows with `outcome="delivered"`
- **THEN** the response's `by_source` includes an entry for `secrets_lifecycle` with `suppressed=120`, `delivered=0`, and `suppressed_never_delivered=true`
- **AND** `"secrets_lifecycle"` appears in the response's `flagged_sources` list

#### Scenario: A healthy source is not flagged
- **WHEN** an `origin_butler` has both `delivered > 0` and `suppressed > 0` rows in the window
- **THEN** its `suppressed_never_delivered` is `false`

#### Scenario: List endpoint is windowed and filterable
- **WHEN** `GET /api/attention/ledger?since=<t1>&until=<t2>&outcome=suppressed&origin_butler=secrets_lifecycle` is called
- **THEN** only rows with `occurred_at` between `t1` and `t2`, `outcome="suppressed"`, and `origin_butler="secrets_lifecycle"` are returned, newest-first, paginated

#### Scenario: Unreachable ledger pool degrades honestly
- **WHEN** the ledger's DB pool is unreachable
- **THEN** both endpoints return HTTP 200 with an empty/zero payload and `source_available=false` — never a truthful-looking "no suppression happened" or "no rows match"

#### Scenario: Unmigrated table is a true empty result, not a degraded one
- **WHEN** `public.attention_ledger` does not exist yet (pre-migration database)
- **THEN** both endpoints return an empty/zero payload with `source_available=true` — this is a genuinely-empty state, not a source failure
