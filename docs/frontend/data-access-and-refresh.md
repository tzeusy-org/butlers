# Frontend Data Access and Refresh Contracts

> **Purpose:** Define how the frontend accesses backend data, how queries stay fresh, and the
> error/empty/loading contract.
> **Audience:** Frontend and backend developers working on data flow between the dashboard and API.
> **Prerequisites:** [Information Architecture](information-architecture.md),
> [Response Conventions](../api_and_protocols/response-conventions.md).

## Data Access Model

The frontend talks to the dashboard API over REST through `frontend/src/api/client.ts`.

- Base URL:
  - `import.meta.env.VITE_API_URL` if set
  - otherwise `/api`
- All requests are JSON and typed.
- Non-2xx responses throw `ApiError` with:
  - `code`
  - `message`
  - `status`

## Query and Refresh Behavior

Default QueryClient behavior (`frontend/src/lib/query-client.ts`):

- `staleTime`: 30s
- `retry`: 1
- `refetchIntervalInBackground`: false — interval polls stop while the document is hidden.

Hidden-tab polling requires the explicit `POLL_IN_BACKGROUND` token from
`frontend/src/lib/poll-policy.ts`, with a local rationale. It is not a default
freshness mechanism.

### Freshness model

Server state reaches the page by one of two paths, and every `refetchInterval` names which one it
is (a lint rule forbids bare numeric intervals; tokens live in `frontend/src/lib/poll-policy.ts`):

- **Bus-covered queries.** The fleet event bus (`WS /api/events/stream`) patches or invalidates
  the query's cache key directly; the per-event-type mapping is
  `frontend/src/hooks/event-cache-registry.ts`. The interval is only a reconciliation sweep
  (`POLL_BUS_RECONCILE_MS`). Use `useBusAwarePollInterval()` so the query falls back to a fast
  cadence while the bus is disconnected — a dead socket must degrade to honest polling, never to
  silent staleness.
- **Non-bus queries.** When no bus event covers the data, the hook declares its own local
  `*_POLL_MS` constant with a one-line reason next to it, or omits the interval for data that is
  only refreshed on demand (detail fetches, config, heavy aggregates).

Adding a live-updating surface means adding one entry to the event cache registry, not a bespoke
WebSocket hook. The hook file is the authority for any specific cadence.

## Writes

Mutations go through `apiFetch` like reads and invalidate or optimistically patch the affected
query keys (`frontend/src/hooks/use-optimistic-mutation.ts`). Which surfaces write is part of each
page's spec under `openspec/specs/dashboard-*`.

### Connector lifecycle writes

Connector detail uses the separate `connector-lifecycle` client and hook for pause and resume.
Resume calls the existing `run-now` endpoint only for a paused connector: it clears pause to
`unknown` until the next heartbeat, without forcing an immediate poll. Both lifecycle controls
stay disabled through the POST and detail/summaries refresh; settings and recovery remain usable.
State comes from confirmed reads, with no optimistic patch. Errors retain the last confirmed
state, show the server's explanation (including a 409's actual state), and refresh authority.
POSTs never retry automatically. A deliberate retry reads state again before another write;
an unavailable read prevents the write. In-flight results and errors stay scoped to their route
identity across navigation. A stale cached detail remains visible if its refresh fails.

The existing server routes attempt their audit append after committing the state change.
Audit is best-effort: append failure is logged without undoing the change or failing the
successful response. No receipt ID or guaranteed audit-recorded status is exposed by the UI.

## Error, Empty, and Loading Contracts

- **Loading:** skeleton placeholders.
- **Empty:** an explicit empty-state message with context — only for a genuine empty result.
- **Error:** explicit error text with a retry that calls the query's `refetch`. Cached data may
  remain visible, labelled stale.
- **Degraded:** a partially failed source is never rendered as empty or all-clear; follow the
  frontend obligation in
  [Response Conventions](../api_and_protocols/response-conventions.md#frontend-obligation).

## Implementation Notes

- New system tiles compose `Query` from `components/ui/query-boundary.tsx` through
  `QueryTile` in `components/ui/query-tile.tsx`. Pass the complete typed hook result;
  the data callback executes only for confirmed success. Pending, paused-without-data,
  refetch errors with cached data, and previous-key placeholders cannot invoke it or
  establish a genuine empty result. Successful background refresh keeps the confirmed
  content visible with a busy indicator. Tile-specific source availability and formatting
  remain in the success presentation; a successful HTTP envelope is not proof every
  source is healthy. `QueryTile` owns the shared error and query-refetch Retry control.
  These primitives compose within `Page`; they do not own page chrome or fetching.
- Where a surface requires retained stale rows, render them as a separate, explicitly
  labelled sibling from a last-confirmed-success snapshot scoped to its originating
  query/filter and authority. Do not supply an error-data callback to `Query` or let a
  previous-filter placeholder count as confirmation for a new filter. Existing system
  tiles continue to replace content on errors; this slice adds no snapshot cache.
- `EventBusProvider` supplies the snapshot cache policy to `useEventStream`: the existing
  event registry plans the same targeted filters, and `applyFleetEventBatch` unions filters
  only when their refetch policies match. Ingestion retains `cancelRefetch: false`; activity
  streams and histograms remain covered. Live messages still use `applyFleetEvent`, with a
  250ms fixed window for ingestion. Snapshot callbacks keep replay order and metadata, and
  reentrant live frames wait until the entire replay completes. Invalid entries are isolated;
  malformed outer envelopes cannot establish healthy freshness. Disconnect, replacement and
  unmount discard pending work from the old socket.
- Standing-condition QA suppression counts use the existing dispatch-events endpoint with
  `decision=infra_condition_open` and each distinct infrastructure `fingerprint`. The hook reads
  the filtered `meta.total` while requesting one event, so older matches and totals above a
  page limit remain exact without downloading the event history. Only infrastructure rows
  participate. Pending totals show loading; failed queries or invalid totals show degradation
  and withhold counts, including after a failed refetch with cached data. A successful zero
  omits the suppression chip. The existing 30-second foreground polling cadence is retained.
- The router is created with the sanitised `import.meta.env.BASE_URL` as its basename
  (`frontend/src/router-config.tsx`), so subpath deployments work for direct loads and in-app links.
- `SessionDetailDrawer` normalises tool-call records before rendering (name under
  `name|tool|tool_name` or nested `call|tool_call|toolCall|function`; arguments under
  `input|args|arguments|parameters`; results under `result|output|response`) and falls back to a
  raw payload block, so `Tool Calls (N)` is never empty. The Codex adapter's
  `src/butlers/core/runtimes/codex.py::_extract_tool_call`
  treats nested `tool` objects the same way.

## Related Pages

- [Response Conventions](../api_and_protocols/response-conventions.md) -- Envelopes, pagination,
  degraded-source flags
- [Information Architecture](information-architecture.md) -- Navigation rationale

### Browser-local unsent input

Approved unsent form bundles use the native IndexedDB task seam, independent of
QueryClient and API response caches. See [browser-local unsent drafts](unsent-drafts.md)
for the exact allowlist, one-second dismissal contract, stale-writer fences, privacy
boundary, and the explicitly unavailable chat outcome-recovery prerequisite.
