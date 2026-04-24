# Google Health Connector — Research Notes

Living document for implementation-time discoveries per `tasks.md §1`. The
items below were resolved while building the E2 connector (bu-k5l35.2)
against the scaffolding E1 (bu-k5l35.1) had landed.

When the API surface firms up further, revisit the "assumed" items and
update the connector + spec in the same PR, per the task-§1 directive.

## Endpoint paths (`tasks.md §1.1`)

Assumption (to be confirmed against live Google Health API once verification
clears test mode):

| Resource bundle | Assumed path (relative to `https://health.googleapis.com/v4`) |
|---|---|
| Sleep sessions | `/users/me/dataTypes/sleep/sessions` |
| Daily activity | `/users/me/dataTypes/activity/daily` |
| Daily resting HR | `/users/me/dataTypes/heartRate/resting/daily` |
| Daily HRV | `/users/me/dataTypes/heartRateVariability/daily` |
| Daily SpO2 | `/users/me/dataTypes/oxygenSaturation/daily` |
| Daily breathing rate | `/users/me/dataTypes/respiratoryRate/daily` |
| VO2 max | `/users/me/dataTypes/vo2Max/daily` |

The `RESOURCE_BUNDLES` table in `src/butlers/connectors/google_health.py`
uses these as defaults. The paths follow Google's `dataTypes/...` convention
signalled in the migration guide; the final leaf segments (`sessions`,
`daily`) mirror the shape of the legacy Fitbit endpoints they succeed.

If live responses come back 404, adjust the `endpoint_path` field on each
`ResourceBundle` (one-line fix) and note the correction here.

## Scope URLs (`tasks.md §1.2`)

Confirmed full scope URLs (also present in
`GOOGLE_SCOPE_SETS['health']` in `src/butlers/api/routers/oauth.py`):

- `https://www.googleapis.com/auth/googlehealth.sleep`
- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`

Stored verbatim in `public.google_accounts.granted_scopes` by the existing
OAuth callback pipeline — no short-form conversion.

Read vs. write: Google Health scope strings are read-only by default (v4
API is a read API); writes require a separate `.write` suffix that is not
needed by this connector.

## Rate-limit headers (`tasks.md §1.3`)

The client in `google_health_client.py` captures the following headers
when present on a response and exposes them via
`GoogleHealthClient.last_rate_limit_headers`:

- `Retry-After` — preferred on HTTP 429. Parsed as integer seconds.
- `X-RateLimit-Remaining` — emitted as the
  `connector_google_health_rate_limit_remaining` Prometheus gauge.
- `X-RateLimit-Reset` — captured but not yet surfaced as a gauge; the
  dashboard can read the raw header via the `/health` endpoint if needed.

On 429 with no `Retry-After`, the connector falls back to
`exponential_backoff_delay(attempt)` (30 s → 600 s, ±25% jitter).

## OAuth client augmentation (`tasks.md §1.4`)

**Resolved in E1.** The same OAuth client handles Google Health scopes —
no separate client is required. `GOOGLE_SCOPE_SETS['health']` is opt-in
via `scope_set=health`; it is never mixed into `_DEFAULT_SCOPES`.

Google will require a privacy + security review before the client can be
moved from test mode to production for Google Health scopes. Per
`design.md §D8` this is deferred as a non-blocking deliverable.

## Reconciled Stream semantics (`tasks.md §1.5`)

The connector passes `view=reconciled` on every data-type query (see
`GoogleHealthConnector._build_params`). When the endpoint does not support
the parameter, Google returns the default (per-source) stream — the
connector still functions; the Reconciled Stream is a strict improvement
when available but not a hard requirement.

## Google user identifier choice

`source.endpoint_identity` and `sender.identity` both carry the owner's
**email** (not the numeric Google `sub`/`id`). Rationale:

- `public.google_accounts` persists `email` as the stable identifier. The
  numeric `id` is fetched only at userinfo time and is not preserved.
- RFC 0004 identity resolution depends on a `public.contact_info(type,
  value)` row existing before envelope ingestion. The OAuth callback
  upserts `(type='google_health', value=<email>)` during pairing for
  `scope_set=health`.
- Switching to the numeric `id` later is a one-row upsert + a cursor
  endpoint-identity migration — tractable if Google ever deprecates
  email-as-identifier for wellness.

The `<google_user_id>` placeholder in the spec is satisfied by the email
today. The spec text says "Google's canonical user identifier for that
account" — email qualifies.

## Cursor key shape

`cursor_store` uses a 2-tuple `(connector_type, endpoint_identity)` key.
Per-resource dimension is encoded into the `endpoint_identity` SUFFIX
(`google_health:user:<email>:<resource>`). The envelope's
`source.endpoint_identity` stays canonical
(`google_health:user:<email>`) — only the cursor key carries the suffix.

See `_cursor_endpoint_identity` and `_endpoint_identity_for_user` helpers.

## Heartbeat state model

The connector reports only `healthy | degraded | error` states per
`connector-base-spec` v2 — no `broken` state anywhere in the code or the
health endpoint payload. The mapping is:

| Internal flag | Heartbeat state | error_message |
|---|---|---|
| `_auth_error=True` | `error` | `token_invalid` (or custom) |
| `_account_missing=True` | `degraded` | `no_primary_account` |
| `_scope_missing=True` | `degraded` | `scope_missing` |
| `_last_source_api_ok=False` | `degraded` | `source_api_unreachable` |
| (otherwise) | `healthy` | `None` |

Verified by `test_health_state_never_emits_broken_string` in
`tests/connectors/test_google_health_connector.py`.
