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

## Error, Empty, and Loading Contracts

- **Loading:** skeleton placeholders.
- **Empty:** an explicit empty-state message with context — only for a genuine empty result.
- **Error:** explicit error text with a retry that calls the query's `refetch`. Cached data may
  remain visible, labelled stale.
- **Degraded:** a partially failed source is never rendered as empty or all-clear; follow the
  frontend obligation in
  [Response Conventions](../api_and_protocols/response-conventions.md#frontend-obligation).

## Related Pages

- [Response Conventions](../api_and_protocols/response-conventions.md) -- Envelopes, pagination,
  degraded-source flags
- [Information Architecture](information-architecture.md) -- Navigation rationale
