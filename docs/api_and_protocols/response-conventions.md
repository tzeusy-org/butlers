# Dashboard API Response Conventions

> **Purpose:** Cross-cutting rules every dashboard API endpoint (and its frontend consumer) must follow: where the API root lives, how lists paginate, and how a partially-failed fan-out must announce itself.
> **Audience:** Anyone adding or changing a dashboard API endpoint, or rendering its payload.
> **Prerequisites:** [Dashboard API](dashboard-api.md).

## Envelopes and errors

The shared wrappers live in `src/butlers/api/models/__init__.py`: `ApiResponse[T]`
(`{"data": T, "meta": {...}}`), `PaginatedResponse[T]` (offset/limit with `meta.total`),
`CursorPaginatedResponse[T]` (see below), and `ErrorResponse` (`{"error": {"code", "message",
"butler"?, "details"?}}`). New endpoints use these wrappers. A few domain routers (for example
timeline and relationship) return unwrapped typed payloads; each route's `response_model` is the
authority, surfaced in the generated OpenAPI schema. Error codes are stable machine strings;
clients branch on `error.code`, never on `message`.

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
- `GET /api/memory/gaps` follows the same keyset shape (`asked_at DESC, id DESC`, merged across
  memory pools) and adds `meta.limit` and `meta.sources_degraded` for pools that failed.

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

## Implementation Notes

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

## Proposed count-bucket truth extension

Count-strip responses declare one UTC as_of/window and structured bucket_start/bucket_end keys. Event counts and listening have separate availability. Measured count0 requires an authoritative count read; unreadable counts are null/unavailable. Listening is live from an exact accepted heartbeat, deaf only from positively complete closed recording with no heartbeat, and unknown otherwise. Omitted/legacy cached metadata is not inferred as live. Relative labels locate the actual declared window and owner-zone display uses the existing AppTimezone context. No new dashboard write is introduced. Optional heartbeat query failure preserves count positives through a savepoint and reports a closed source-degraded reason, never exception text. This describes the released source contract; PostgreSQL proof, elapsed recording and final native adoption remain independently verified obligations.

### Proposed rolling-window and proof precision

The 24h count projection has 24 complete one-hour rolling intervals ending at a post-lock captured database `as_of`; its final slot is `[as_of-1h,as_of)`. A separate open calendar-hour diagnostic is partial/UNKNOWN, not the final rolling bar. Compatible catalog/endpoint locks preserve the receiver snapshot without dashboard DML or a global endpoint mutex. Seeded past API/FE timeline fixtures prove projection conformance only; genuinely elapsed protected recording and ordinary-role admission require their own production-clock/writer evidence. No caller-provided clock or fixture marker supplies production authority.


### Source-declared sparse count windows

Connector detail projects the stats response's exact `window_start`, `window_end`, `bucket_width_s` and `hourly_events_available` into its count-strip window. The roster passes the same fields from the summaries response's `data.bucket_window` to every row. The shared primitive places sparse source keys within those declared bounds; it does not stretch two observations into adjacent cells or infer a 24-hour span from their endpoints. Missing cells carry unknown listening and unavailable counts unless the source explicitly declares a successful complete count read. Invalid bounds, overlapping/duplicate keys or out-of-window rows make the window unavailable. Older keyed responses without a declared window retain their actual supplied cells, including genuinely short windows. Unkeyed numeric compatibility responses still cannot supply a clock axis.
