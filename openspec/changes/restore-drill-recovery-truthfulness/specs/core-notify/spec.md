## ADDED Requirements

### Requirement: Restore-Drill Attention Provenance
The attention ledger SHALL admit a `restore_drill` source for a durably
recorded restore-drill failure. That source SHALL use `outcome="failed"`, a
stable failure code as its reason, and metadata limited to the stable failure
stage/code, a bounded sanitized detail, and the recorded-at reference. It MUST
not claim notification delivery: channel and intent are null and
`notification_ref` is null.

ID: REQ-core-notify-026
Source: RFC 0005 § Workflow and Recovery Telemetry; RFC 0011 Amendment 1; system-overview-page REQ-system-overview-page-006
Scope: v1-mandatory

#### Scenario: Durable failed drill gets a truthful ledger event
- **WHEN** a restore-drill failure has been durably written to its
  executor-owner result authority ledger
- **THEN** the job attempts a `public.attention_ledger` insert with
  `source="restore_drill"`, `outcome="failed"`, a stable failure-code reason,
  and null channel, intent, and notification reference
- **AND** its metadata contains only stable/sanitized provenance rather than raw
  stderr, a credential, a connection string, dump content, or an unbounded path

#### Scenario: Pass and no-result do not impersonate attention failures
- **WHEN** a restore drill passes or finds no backup file to attempt
- **THEN** it writes no `source="restore_drill", outcome="failed"` attention
  event
- **AND** it does not write a synthetic `notify` event or notification reference

#### Scenario: Ledger failure preserves the recorded restore result
- **WHEN** the restore-drill authoritative result is durable but the
  attention-ledger write fails
- **THEN** the authoritative result and its result-aware retry cadence remain intact
- **AND** the ledger failure is logged as an observability failure without
  changing the drill result or claiming an owner notification

## MODIFIED Requirements

### Requirement: Attention Ledger Reader
The dashboard API SHALL expose a windowed, filterable reader over
`public.attention_ledger` and a per-source delivery-vs-suppression summary, so
that a source silently failing at a notification, insight, discretion, or
restore-drill boundary is observable instead of requiring direct DB access.
`GET /api/attention/ledger` SHALL return a paginated, newest-first list of
ledger rows, filterable by `intent`, `source` (the ledger's boundary vocabulary:
`notify`/`insight` for proactive egress, `discretion` for the connector
failover-exhausted inbound suppression, and `restore_drill` for a durable
recovery-proof failure), `outcome`, and `origin_butler`, and windowed by
`since`/`until` (`occurred_at` bounds). `GET /api/attention/ledger/summary`
SHALL return, for a `since`/`until` window (defaulting to the last seven days
when `since` is omitted), one row per distinct `origin_butler` with
`delivered`/`coalesced`/`deferred`/`suppressed`/`failed`/`total` counts and a
`suppressed_never_delivered` boolean: `true` when that `origin_butler` has
`suppressed > 0` and `delivered == 0` in the window. Both endpoints MUST follow
the repo's degraded-envelope convention: a genuinely unreachable ledger pool
renders `source_available=false` on an otherwise-empty/zero payload, never a
truthful "no suppression" or "no rows" result.
The Trust Console panel that renders this summary (`AttentionLedgerPanel`) MUST
render a non-zero `failed` count in a visually distinct alerting treatment,
never the same neutral treatment as `coalesced`, `deferred`, or `total`, because
a failed count represents genuine, un-retried breakage in a user-facing or
recovery-proof boundary.
The summary's "per source" grouping is `origin_butler` (the butler or job that
attempted the work), a distinct dimension from the ledger's `source` boundary
literal. Both are independently exposed: `origin_butler` is the summary grouping
key and optional list filter; `source` is a list and summary filter.

ID: REQ-core-notify-008
Source: RFC 0005 § Workflow and Recovery Telemetry; RFC 0011 Amendment 3
Scope: v1-mandatory

#### Scenario: Suppressed-but-never-delivered source is flagged
- **WHEN** `GET /api/attention/ledger/summary` is called for a window in which
  `origin_butler="secrets_lifecycle"` has 120 rows with `outcome="suppressed"`
  and 0 rows with `outcome="delivered"`
- **THEN** the response's `by_source` includes an entry for `secrets_lifecycle`
  with `suppressed=120`, `delivered=0`, and `suppressed_never_delivered=true`
- **AND** `"secrets_lifecycle"` appears in the response's `flagged_sources` list

#### Scenario: A healthy source is not flagged
- **WHEN** an `origin_butler` has both `delivered > 0` and `suppressed > 0` rows
  in the window
- **THEN** its `suppressed_never_delivered` is `false`

#### Scenario: Failed deliveries are counted separately from deferred
- **WHEN** `GET /api/attention/ledger/summary` is called for a window in which
  `origin_butler="secrets_lifecycle"` has 21 rows with `outcome="failed"` and
  0 rows with `outcome="deferred"`
- **THEN** the response's `by_source` entry for `secrets_lifecycle` has
  `failed=21` and `deferred=0`, and the two counts are never merged

#### Scenario: Restore-drill source is filterable without notification provenance
- **WHEN** `GET /api/attention/ledger?source=restore_drill&outcome=failed` is
  called for a window containing a recorded restore-drill failure
- **THEN** the returned newest-first row exposes `source="restore_drill"`,
  `outcome="failed"`, its stable sanitized provenance, and a null notification
  reference
- **AND** the caller can distinguish it from a failed `notify` delivery

#### Scenario: List endpoint is windowed and filterable
- **WHEN** `GET /api/attention/ledger?since=<t1>&until=<t2>&outcome=suppressed&origin_butler=secrets_lifecycle`
  is called
- **THEN** only rows with `occurred_at` between `t1` and `t2`,
  `outcome="suppressed"`, and `origin_butler="secrets_lifecycle"` are returned
  newest-first and paginated

#### Scenario: Unreachable ledger pool degrades honestly
- **WHEN** the ledger's DB pool is unreachable
- **THEN** both endpoints return HTTP 200 with an empty/zero payload and
  `source_available=false`, never a truthful-looking "no suppression happened"
  or "no rows match"

#### Scenario: Unmigrated table is a true empty result, not a degraded one
- **WHEN** `public.attention_ledger` does not exist yet on a pre-migration database
- **THEN** both endpoints return an empty/zero payload with
  `source_available=true` because this is a genuinely-empty state

### Source References

- RFC 0005 § Workflow and Recovery Telemetry: durable structured evidence and
  low-cardinality failure classification support recovery truthfulness.
- RFC 0011 Amendments 1 and 3: the attention ledger is an honesty surface whose
  source and outcome vocabulary must not fabricate delivery.
- RFC 0007 § Response Envelope: a degraded ledger read is visibly unavailable,
  not a truthful empty result.
