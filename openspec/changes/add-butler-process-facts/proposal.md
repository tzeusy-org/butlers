## Why

The Dispatch mockup proposes a `pid` field for the butler Overview surface, but the dashboard API runs in a separate Docker service from `butlers-up`, with `BUTLERS_HOST: butlers-up` configured for dashboard reachability (`docker-compose.yml:162-182`) while `butlers-up` starts all butler daemons inside its container (`docker-compose.yml:467-473`). A per-process `pid` would be from the daemon container namespace, not the dashboard container namespace, and would change on every restart, so it is not stable or actionable for an operator. The Overview process card therefore SHALL omit `pid` and surface `container_name`, `port`, `registered_duration_seconds`, and `config_path` from existing registry/config surfaces instead.

## What Changes

- Add an Overview tab process facts card for each butler detail page.
- The card shows four fields: `container_name`, `port`, liveness duration rendered from `registered_duration_seconds`, and `config_path`.
- Explicitly reject and exclude `pid` from the API contract, frontend types, and rendered card.
- Extend the backend butler detail data surface with the process facts needed by the frontend, deriving them from the existing connection, registry, heartbeat, and roster configuration sources.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: Adds the Overview tab process facts card requirement and the source-backed field contract for `container_name`, `port`, `registered_duration_seconds`, and `config_path`.

## Impact

- **Backend API**: Extend `ButlerDetail` / `GET /api/butlers/{name}` so the frontend can render process facts without scraping unrelated endpoints. Current detail construction already loads `roster/<name>/butler.toml` and exposes `port` (`src/butlers/api/routers/butlers.py:221-250`).
- **Process source facts**:
  - `port`: already present on `ButlerSummary` and populated from `ButlerConnectionInfo.port` (`src/butlers/api/models/__init__.py:101-117`, `src/butlers/api/routers/butlers.py:124-131`).
  - `container_name`: derive from the runtime MCP host used by dashboard connections, where `ButlerConnectionInfo.sse_url` reads `BUTLERS_HOST` (`src/butlers/api/deps.py:52-60`) and compose sets it to `butlers-up` (`docker-compose.yml:162-182`).
  - `registered_duration_seconds`: derive from switchboard liveness facts, using the registry's `registered_at`/`last_seen_at` columns (`roster/switchboard/migrations/001_switchboard_messaging.py:44-50`), the registry API's selected `registered_at` field (`roster/switchboard/api/router.py:381-384`), and the heartbeat/system surface's `last_seen_at` freshness read (`src/butlers/api/routers/system.py:643-699`).
  - `config_path`: derive from the roster directory and detail loader path, then serialize as the stable roster-relative path `roster/<name>/butler.toml` instead of an absolute host or container filesystem path (`src/butlers/api/routers/butlers.py:58-64`, `src/butlers/api/routers/butlers.py:221-224`).
- **Frontend**: Add a small process facts card to the existing Overview tab card stack, update API types, and add a regression test that the rendered Overview tab includes exactly the expected process fact labels and no process ID row.
- **No database migration required**: the required registry/config data already exists.
