## ADDED Requirements

### Requirement: Channel defaults data model and REST API

The system SHALL store per-channel default policy in `public.channel_defaults`
and SHALL expose a REST surface under `/api/ingestion/channel-defaults` for
reading and updating it. Channel defaults are the policy floor a channel falls
back to when no ingestion rule matches; they belong to the ingestion policy
capability rather than to any dashboard surface, because the evaluator — not
the UI — is their consumer.

Table schema:

- `channel TEXT PRIMARY KEY`
- `default_policy_json JSONB NOT NULL` — an opaque document interpreted by the
  per-channel evaluator
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `updated_by TEXT NOT NULL`

Endpoints:

| Method | Path | Behaviour |
|--------|------|-----------|
| GET | `/api/ingestion/channel-defaults/{channel}` | Returns `{channel, default_policy_json, updated_at, updated_by}`; HTTP 404 when no row exists |
| PATCH | `/api/ingestion/channel-defaults/{channel}` | Validates then upserts, creating the row when missing; returns the stored document |
| DELETE | `/api/ingestion/channel-defaults/{channel}` | HTTP 405 — the route exists solely to make the refusal explicit rather than a routing accident |

The channel name SHALL be validated against a closed set of known channels; an
unrecognized channel SHALL be rejected with HTTP 400 naming the known set,
so a typo cannot silently create an orphan row no evaluator will ever read.
The submitted document SHALL be validated against a per-channel schema
declaring that channel's required keys, and SHALL be rejected with HTTP 400
when a required key is missing, when `priority_action` is outside the allowed
set, or when a channel-specific constraint fails. No row SHALL be mutated by a
rejected request. Every endpoint SHALL return HTTP 503 when the shared database
pool is unavailable.

`PATCH` SHALL replace `default_policy_json` wholesale rather than merging into
the stored document; the verb is a concession to the route shape, not a
statement about partial update semantics.

`updated_by` SHALL be carried on the request body and SHALL default to
`dashboard`. It is caller-asserted provenance, not an authenticated identity,
and SHALL NOT be relied on as proof of who made the change; the audit trail
records the same caller-asserted actor alongside the originating client
address, which is the load-bearing attribution.

A successful update SHALL emit an audit entry with
`action = 'ingestion.channel_default.update'` and the channel as target. The
audit write SHALL be best-effort: a failure SHALL be logged and SHALL NOT fail
the update.

The table SHALL have no TTL and no pruning job; entries persist until
explicitly overwritten.

#### Scenario: GET returns the channel defaults

- **WHEN** `GET /api/ingestion/channel-defaults/email` is called and a row exists
- **THEN** the response body is `{channel, default_policy_json, updated_at, updated_by}`

#### Scenario: GET returns 404 for a channel with no row

- **WHEN** `GET /api/ingestion/channel-defaults/{channel}` is called and no row exists
- **THEN** the response is HTTP 404

#### Scenario: PATCH upserts

- **WHEN** `PATCH /api/ingestion/channel-defaults/email` is called with a valid document
- **THEN** the row is inserted, or updated in place on conflict with `updated_at` refreshed
- **AND** the response returns the stored document

#### Scenario: PATCH rejects an unknown channel

- **WHEN** the path names a channel outside the known set
- **THEN** the response is HTTP 400 naming the known channels
- **AND** no row is created

#### Scenario: PATCH rejects a missing required key

- **WHEN** the document omits a key required for that channel
- **THEN** the response is HTTP 400 naming the missing keys
- **AND** no row is mutated

#### Scenario: PATCH rejects an invalid priority action

- **WHEN** the document sets `priority_action` to a value outside the allowed set
- **THEN** the response is HTTP 400 naming the allowed values

#### Scenario: PATCH replaces rather than merges

- **WHEN** a stored document has keys absent from a subsequent valid PATCH body
- **THEN** the stored document is replaced by the submitted one and the absent
  keys are gone

#### Scenario: Update emits an audit entry

- **WHEN** a channel default is updated
- **THEN** an audit entry is written with
  `action = 'ingestion.channel_default.update'` and the channel as target
- **AND** an audit failure is logged without failing the update

#### Scenario: No DELETE surface

- **WHEN** a caller attempts `DELETE` on a channel-defaults path
- **THEN** the response is HTTP 405
- **AND** no row is removed

#### Scenario: No TTL job exists

- **WHEN** the retention jobs are reviewed
- **THEN** none of them targets `public.channel_defaults`
