## Why

`GET /api/steam/connector/health` proxied the Steam connector's own `/health` listener
on `127.0.0.1`. That listener is loopback-only by design, and on Compose and k3s the
connector runs in another container or pod, so the proxy reported `not_running` whatever
the connector's real state. Loosening the bind is a rejected security-posture change.
The per-account `connector_health` on the account status route read the same proxy and
could never be populated there either.

## What Changes

- Connector Health View: the endpoint reports the connector's state from its newest
  `switchboard.connector_registry` heartbeat row (the source the other connector status
  cards use) and makes no HTTP call to the connector.
- A stale, offline, future-dated or absent heartbeat reads `not_running`; an unreadable
  registry reads `unknown` with `registry_available=false`, so a failed source is never
  presented as a truthful "not running".
- `active_accounts` is null and `account_health` is empty: the account set a heartbeat
  carries is frozen at connector start and the registry records no per-account detail.
- Account Status Endpoint: `connector_health` is null. The registry records one
  connector-wide state, which must not be attributed to a single account.

## Impact

- Spec: `dashboard-steam` (MODIFIED "Account Status Endpoint", "Connector Health View").
- Code: `src/butlers/api/routers/steam.py`, `src/butlers/api/models/steam.py`.
- No frontend code reads the route or `connector_health`; no user-visible behaviour
  changes on a containerized deploy, where the old answer was always `not_running`.
- Follow-up: bu-clre0t refreshes the heartbeat identity on rescan, after which a
  per-account state could be attributed again.
