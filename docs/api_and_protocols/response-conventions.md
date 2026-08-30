# Dashboard API Response Conventions

> **Purpose:** Cross-cutting rules every dashboard API endpoint (and its frontend consumer) must follow: where the API root lives, how lists paginate, and how a partially-failed fan-out must announce itself.
> **Audience:** Anyone adding or changing a dashboard API endpoint, or rendering its payload.
> **Prerequisites:** [Dashboard API](dashboard-api.md).

## Mount boundary

The dashboard API is mounted at `/api` locally but may be path-mounted or use an absolute
`VITE_API_URL` in deployed environments. Backend payloads that contain an API endpoint for the
browser to follow must return the path **below the API root** (for example,
`/data/export/download/...`), never a site-rooted `/api/...` path.

Frontend requests use `apiFetch`; browser navigation or download links returned by the backend use
`resolveApiHref`. Keep frontend routes separate from this convention.

## Cursor pagination

`GET /api/ingestion/events` uses **cursor pagination** — the `page` and `offset` params are gone
(use `limit` and the opaque `cursor` from the preceding response instead). For the default
recent sort, the cursor is a keyset position ordered by `received_at DESC, id DESC`.

Response envelope:

```json
{"data": [...], "meta": {"next_cursor": "<opaque>", "has_more": true}}
```

- Pass `cursor=<next_cursor>` to fetch the next page; `next_cursor` is `null` on the last page.
- `has_more: false` means you are at the last page.
- No `total` or `offset` field is returned.
- The optional `sort=cost` view keeps this cursor-shaped envelope but its opaque cursor encodes
  a page offset; do not mix cursors between sort modes.

Channel filtering uses `channels` as the primary comma-separated source-channel filter. The server
still accepts the deprecated single-value `source_channel` query parameter for compatibility, but
it is server-only and is not exposed by the private frontend client. When both parameters are
present, `channels` takes precedence over `source_channel`.

## Degraded-mode response envelope

Endpoints that query Prometheus for aggregate metrics (`GET /api/ingestion/pipeline?window=24h`,
`GET /api/ingestion/connectors/cross-summary`) always return HTTP 200. When Prometheus is
unreachable, aggregate fields contain zeros and the envelope includes:

```json
{"...", "aggregates_available": false}
```

Never treat a missing or `false` `aggregates_available` field as an error — show a "metrics
unavailable" indicator in the UI instead. (Phase 4a, PRs #1762, #1798.)

### Fleet-wide convention (bu-qvnce.1, 2026-07-04)

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

## Related Pages

- [Dashboard API](dashboard-api.md) --- application factory, router discovery, SSE streaming
- [Backend API Contract](../frontend/backend-api-contract.md) --- per-domain endpoint contracts consumed by the frontend
