# Dashboard API Response Conventions

> **Purpose:** Cross-cutting rules every dashboard API endpoint (and its frontend consumer) must follow: where the API root lives, how lists paginate, and how a partially-failed fan-out must announce itself.
> **Audience:** Anyone adding or changing a dashboard API endpoint, or rendering its payload.
> **Prerequisites:** [Dashboard API](dashboard-api.md).

## Envelopes and errors

The shared wrappers live in `src/butlers/api/models/__init__.py`: `ApiResponse[T]`
(`{"data": T, "meta": {...}}`), `PaginatedResponse[T]` (offset/limit with `meta.total`),
`Page[T]` from `src/butlers/api/pagination.py` (cursor lists, see below), and `ErrorResponse` (`{"error": {"code", "message",
"butler"?, "details"?}}`). New endpoints use these wrappers. A few domain routers (for example
timeline and relationship) return unwrapped typed payloads; each route's `response_model` is the
authority, surfaced in the generated OpenAPI schema. Error codes are stable machine strings;
clients branch on `error.code`, never on `message`.

`frontend/src/api/schema.gen.ts` is generated from this native OpenAPI contract
with pinned `openapi-typescript`; edit the Python authority, then regenerate it.
Response models may inherit `ResponseSchemaModel` to describe default-valued
fields that are emitted during serialization without making request fields
mandatory. Routes retaining dictionary responses can publish a native output
model as metadata while preserving their existing runtime validator; such a
model does not add runtime response validation. Do not replace explicit
`response_model_exclude_*` behavior or an extensible metadata bag to satisfy the
TypeScript compiler.

Endpoint-specific metadata models use opt-in envelopes and retain arbitrary
extension keys, nulls, nested aliases and the original direct-Python return type.
Shared `ApiResponse`/`ApiMeta` behavior remains unchanged. Regeneration is static:
`npm run generate:api` and `npm run check:api` need the checkout's installed Python
package and frontend dependencies, but no running API, database or provider.

Shared query and persistence primitives live below the API package. The insights read-model
re-exports `butlers.insight_delivery_state`; sessions re-exports its unchanged legacy cursor from
`butlers.session_cursor`; `butlers.api.conversations` re-exports shared message/reply writers from
`butlers.conversation_messages`. Request-scoped NOTIFY publication lives in
`butlers.chat_stream_events`, while SSE listening remains in `butlers.api.chat_stream`.
These API facades retain existing consumers without making modules depend on the HTTP layer.

API routes do not redirect on a trailing slash (`redirect_slashes = False` in
`src/butlers/api/app.py`): register and call the exact path.

## Mount boundary

The dashboard API is mounted at `/api` locally but may be path-mounted or use an absolute
`VITE_API_URL` in deployed environments. Backend payloads that contain an API endpoint for the
browser to follow must return the path **below the API root** (for example,
`/data/export/download/...`), never a site-rooted `/api/...` path.

Frontend requests use `apiFetch`; browser navigation or download links returned by the backend use
`resolveApiHref`. Keep frontend routes separate from this convention.

## Cursor pagination

Every cursor-paginated list returns the one **`Page[T]`** envelope and builds its cursor with the
one codec, both in `src/butlers/api/pagination.py`. A new list endpoint uses them; it does not add a
second envelope or a second codec.

```json
{"items": [...], "next_cursor": "<opaque>", "limit": 50}
```

- Pass `cursor=<next_cursor>` to fetch the next page. `next_cursor` is `null` on the last page, so
  "is there more" is `next_cursor !== null`; there is no `has_more`, `total` or `offset`.
- `limit` echoes the effective page size. `sources_degraded` (omitted when every source answered)
  names fan-out sources that failed, so a partial page never reads as the whole truth.
- An endpoint that returns more than the page subclasses `Page` and adds sibling fields; it does
  not wrap the page in a second object.
- The canonical API facade is `butlers.api.pagination`; its one shared codec implementation
  lives in transport-neutral `butlers.pagination`, which core queries can use without importing
  the API. The facade translates only its specific cursor error (`ValueError` in core) to HTTP
  400 and preserves the public functions and `Page` envelope.
- The cursor is `encode_cursor(values)` / `decode_cursor(cursor, shape)`: a versioned, URL-safe,
  checksummed token over the last row's keyset values (`datetime`, `UUID`, `str`, numbers).
  `decode_cursor` returns the same types it was given and raises `InvalidCursorError`, a 400, for
  anything malformed, edited, from another codec version, or minted by an endpoint with a
  different receiver scope or exact `shape`. Each producer and receiver supplies a fixed
  server-owned scope; caller query parameters cannot choose it. The shared decoder requires
  canonical unpadded URL-safe base64 and exact scalar types (a boolean is not an integer offset).
  The scoped token layout is version 2. Treat cursors as opaque; they are not stable across deployments that change
  the codec version, and a 400 means "start again from the first page". Before the shared codec,
  fleet cases, message search and ingestion `sort=cost` answered an invalid cursor with 422; they
  now answer 400 like every other cursor route, and cursors minted before the change are invalid.
- `GET /api/ingestion/events` orders by `received_at DESC, id DESC`. Its optional `sort=cost` view
  keeps the same envelope but its cursor encodes a page offset, so a cursor from one sort mode is a
  400 in the other.
- `GET /api/memory/gaps` orders by `asked_at DESC, id DESC`, merged across memory pools.

**Offset pagination** (`PaginatedResponse[T]`, `meta.total/offset/limit`) stays on the many list
endpoints whose pages are random-access with an exact `total` (health and finance registers,
approvals, audit log and the like). A new offset endpoint uses `PaginatedResponse`; a new
cursor endpoint uses `Page`.

**Routes still off `Page`** are listed, with reasons, in
`tests/api/test_pagination.py` (`test_every_paginated_route_uses_page_or_a_listed_exception`), which
fails when a paginated route uses any other envelope or when a listed route converts without
dropping its entry. Today that is `GET /api/sessions` (`KeysetResponse`, retires at the RFC 0038
D19 switch), `GET /api/timeline`, the calendar workspace read and invitations routes, and the QA
case journal tail-follow, plus a handful of bespoke offset envelopes.

Channel filtering uses `channels` (comma-separated); there is no single-value channel alias.

## Degraded-mode response envelope

Endpoints that query Prometheus for aggregate metrics (`GET /api/ingestion/pipeline?window=24h`,
`GET /api/ingestion/connectors/cross-summary`) always return HTTP 200. When Prometheus is
unreachable, aggregate fields contain zeros and the envelope includes:

```json
{"...", "aggregates_available": false}
```

Never treat a missing or `false` `aggregates_available` field as an error — show a "metrics
unavailable" indicator in the UI instead.

### Fleet-wide convention

Every fan-out/aggregation endpoint across the dashboard API follows the same rule: **a source that
raises or is unreachable must never render as a truthful empty/zero/all-clear result.** The concrete
shape of the flag varies by endpoint, matched to whatever response envelope it already returns:

- **Bespoke boolean field on the response model**, mirroring `aggregates_available` — e.g.
  `BoardRow.stripe_source_error` / `BoardAggregates.sessions_source_error`
  (`GET /api/butlers/board`), `NotificationListResponse.source_available` /
  `NotificationStats.source_available` (`GET /api/notifications`, `/stats`), `HeaderCounts` fields
  turning `null` instead of `0` per-field (`GET /api/settings/console`),
  `ProviderConfig.config_available` (`GET /api/settings/providers`, false when a row's stored
  JSONB config is not a JSON object — the entry is still listed, with an empty `config`).
- **`meta.<flag>` on the extensible `ApiMeta`/`PaginationMeta` bag** (both have
  `model_config = {"extra": "allow"}`) — e.g. `meta.pools_failed` (`GET /api/memory/stats`),
  `meta.sources_degraded` (`GET /api/approvals`, `/history`), `meta.catalogue_available`
  (`GET /api/secrets/breaks-catalogue`).
- **A named list on the payload itself** — e.g. `SpendSummary.unavailable_butlers` / the
  `/api/spend/breakdown` dict's `unavailable_butlers` key.

`src/butlers/api/degraded.py::DegradedSources` is a small shared tracker for the common "loop over N
pools/butlers, one raises" shape: `tracker.mark(name)` inside the `except`, then `tracker.failed` /
`tracker.names` at the end of the fan-out. It intentionally does **not** replace per-endpoint field
naming — match the flag name to what the endpoint already calls its failure mode.

### Classify before flagging

A source that is *legitimately* absent (e.g. a butler with no memory tables, a pre-migration table
that does not exist yet via `UndefinedTableError`) is not a degraded source — only flag a *genuine*
failure (dropped connection, timeout, permission error, unreachable pool). See
`memory.py::_is_missing_memory_schema_error` for the reference classifier. Getting this distinction
wrong in either direction reintroduces either false alarms or fabricated calm.

### Frontend obligation

Gate any verdict/all-clear renderer on the relevant flag(s) using the `SourceDegradedNote`
vocabulary (`frontend/src/components/ui/query-boundary.tsx`) — name the degraded source inline
(colon-separated source and reason), never suppress it.

### Section-level partial aggregation

An aggregation with independent query sections keeps the successful sections
when one query fails. Its payload exposes a closed availability state and one
content-blind status entry per section; the failed section uses a safe
compatibility fallback only when the status says unavailable.

For example, the Lifestyle taste summary may return:

```json
{
  "data": {
    "total_works": 0,
    "total_signals": 340,
    "availability": "partial",
    "ledger_available": true,
    "query_availability": [
      {"query": "total_works", "state": "unavailable", "reason": "query_failed"},
      {"query": "total_signals", "state": "available", "reason": null}
    ]
  }
}
```

The frontend renders the failed section as unavailable and preserves the
successful value. A complete response with zero values is a genuine empty
result; when every section is unavailable, the aggregate state is
unavailable, never a genuine empty result. Status entries must not carry
SQL, exception text, credentials, or source payloads.

### Currency-honest money aggregates

An endpoint without an owner-sourced FX rate must aggregate monetary rows by their stored ISO
currency. Additive `by_currency` buckets are the canonical totals. A retained legacy scalar may
remain numeric for wire compatibility, but when more than one currency contributed it must carry
`legacy_aggregate_degraded: true`, `degraded_reason: "multiple_currencies_unconverted"`, and a null
`currency`. Frontends must render the per-currency buckets rather than format that degraded scalar
as if it had one denomination. A single-currency result carries that real currency; an empty result
does not invent USD.

## Notification metadata: one-layer object-or-null compatibility

`NotificationSummary.metadata` uses the same normalizer in all three response paths:
`GET /api/notifications`, `GET /api/butlers/{name}/notifications`, and
`PATCH /api/notifications/{id}/read`. The field remains an object or `null`; it does not expose
arbitrary JSON values or infer missing provenance.

| Decoded database value | Response `metadata` |
| --- | --- |
| Mapping/object | A shallow object copy. |
| `null` | `null`. |
| JSONB string whose one JSON parse yields an object | That parsed object. |
| Malformed string, or a string whose single parse cannot complete because of a JSON decoder safety limit | `{"_raw": <the exact original outer string>}`. |
| String whose one parse yields an array, string, number, boolean or `null` | `{"_raw": <the exact original outer string>}`. |
| Actual non-string, non-object JSONB value, such as an array, number or boolean | `null`, without an `_raw` wrapper. |

The normalizer parses at most once. An outer string that parses to another JSON-looking string
retains the original outer string under `_raw`; it is not decoded again. Decoder-limit failures
(`ValueError` or `RecursionError`, including JSON decoder failures) use the same fallback. This is
read compatibility, not a database rewrite or a change to delivery, retries, effective status,
pagination, source availability, degraded-source handling, mark-read behavior or authorization.
Only the existing authenticated API response may carry `_raw`; operational evidence must never
print that content.

The existing executable owners are the direct and three-path regressions in
`tests/api/test_notifications.py`. The writer is a separate seam: the registered production JSONB
codec must receive a JSON-safe mapping once, including `None` and empty-metadata handling, as
covered by `roster/switchboard/tests/test_tools.py`. The cross-schema delivery test alone does not
prove that normalization. Historical repair remains blocked by the separate
[serving-writer evidence procedure](../operations/notification-metadata-repair.md); a green API or
writer regression is not serving-deployment evidence.

## Implementation Notes

- Connector summaries are owned by `ApiResponse[ConnectorSummaryResponse]` and its explicit nested
  wire models in `api/routers/ingestion_connectors.py`. Keep the existing derivation helpers as the
  authority for liveness, operational roles and source availability. This route excludes unset
  fields so registry failure retains only `connectors` and `connector_registry_available` in data,
  while explicitly supplying `meta` preserves the existing empty outer metadata object. Do not
  materialize secondary-source defaults as evidence when the primary registry read failed.
- `GET /api/audit-log` treats `UndefinedTableError` on `dashboard_audit_log` as an empty page
  (`data=[]`, `total=0`), not a 500, because the dashboard can start against an unmigrated or
  offline switchboard schema.
- The notifications endpoints treat a missing switchboard `notifications` table like an unavailable
  pool: empty pages and zeroed stats, not a 500, before switchboard migrations run.
- Currentness: audit groups and QA patrol failures count as current only inside the closed
  `[now-window, now]` interval (future timestamps excluded), and a failed-notification query and its
  drill-down link share one captured `since`/`until` pair.
- TanStack Query can report `isError` while keeping data from an earlier success: show stale
  freshness beside retained rows instead of replacing them with an error banner.
- Timeline partial sources: keep the generic `meta.degraded_sources` signal and add
  `meta.degraded_butlers` only for named failed session pools (the frontend defaults the additive
  field). When unpinned, commit current rows and cursor before fetching older data, so a failed page
  stays visible and retries the same cursor.

### Known-contact harm availability

`GET /api/ingestion/events/dropped-known` returns `available`, `counts_available`, `classification_available`, nullable `uncertain_drops`, and a closed `availability_reason` alongside counts/window. Overall availability requires both complete reads, valid fresh last-admitted evidence for every applicable Gmail runtime, and zero historical uncertainty. The authoritative registry read uses the owning Switchboard pool; the filtered store uses its shared pool. Checkpoint/archived/deleted/foreign rows do not certify runtime classification; unknown roles, legacy/missing/invalid metadata or read failures are unknown. Only a successful complete read proves no applicable accounts.

Readable positive counts remain lower bounds when classification is unknown. A failed filtered read returns zero placeholders with `counts_available=false`; those are not evidence of no harm. A registry failure preserves readable counts with classification unavailable. Open uncertain Gmail history includes filtered, replay_failed and replay_pending in the received-at window; recovered current evidence does not rewrite old uncertainty. Replay completion or leaving the window may resolve that uncertainty. The actual query/opener retains the count door plus unknown on partial/error states, renders loading status, and shows calm zero only with proven availability. No automatic replay is added.

## Related Pages

- [Dashboard API](dashboard-api.md) --- application factory, router discovery, SSE streaming
- `openspec/specs/dashboard-*` --- required per-domain endpoint behavior
- `src/butlers/api/app.py` and `roster/{butler}/api/router.py` --- the routers and their response models


### Independent bearer scrub availability

`GET /api/ingestion/pipeline?window=1h|24h|7d` adds optional-compatible
`bearer_scrub_counts: {auth_code: number, reset_link: number, magic_link: number} | null`
and `bearer_scrub_available: boolean`. The endpoint preserves the existing flat
funnel and backlog fields and their independent availability flags. Scrub counts
are finite, non-negative estimates of fresh Switchboard scrub operations,
including attempts, rather than unique messages. Fractional Prometheus
`increase` values are preserved.

Only a readable source, including an empty vector, sets scrub availability true;
empty means three measured zeros. Unconfigured, unreadable, negative,
non-finite or unexpected-kind results return HTTP200 with null scrub counts and
false availability without discarding independently healthy funnel or backlog
sections. Prometheus fields share the existing 60-second cache keyed by window;
backlog reads remain fresh on every request.

The Timeline section follows the selected range through the existing API client
and query hook. It names Prometheus, acknowledges loading without blocking the
ledger, and renders a source-unavailable note on request errors, false flags or
absent legacy fields. Retained stale query data during an error does not certify
current scrub availability. An unavailable source never becomes a zero or an
all-clear. No raw metric labels or upstream diagnostic text is exposed in the
scrub projection.
