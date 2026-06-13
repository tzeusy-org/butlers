## Context

The butler detail API already combines roster configuration and live MCP status into `ButlerDetail`. The Overview tab already renders identity and operational cards, but the Dispatch mockup's proposed `pid` field does not survive the dashboard/butler container boundary.

Existing sources are sufficient for stable process facts: roster discovery gives the config path and port, the dashboard connection host gives the daemon container/service name, and the switchboard liveness registry gives heartbeat/registration timestamps.

## Goals / Non-Goals

**Goals:**

- Add a stable process facts object to the butler detail backend response.
- Render `container_name`, `port`, liveness duration from `registered_duration_seconds`, and `config_path` in the Overview tab.
- Keep `pid` out of backend schemas, frontend types, and rendered DOM.

**Non-Goals:**

- Do not introduce Docker socket inspection, Kubernetes API calls, or host process introspection.
- Do not create a log tailing or process-control surface.
- Do not add a database migration unless implementation discovers the existing registry timestamps are insufficient.

## Decisions

1. **Use stable topology facts instead of process IDs.** The dashboard should render facts that retain meaning across restarts and container namespaces. `container_name` should come from the MCP host/service name the dashboard uses to reach the daemon container, falling back to the registry endpoint host when needed.

2. **Put process facts on the butler detail response.** The Overview tab already depends on `GET /api/butlers/{name}` for identity data, and the card is scoped to one butler. Keeping the fields on detail avoids frontend fan-out across list, system heartbeat, switchboard registry, and config endpoints.

3. **Represent liveness duration as a numeric derived fact, not backend-formatted copy.** Implementations can calculate `registered_duration_seconds` from the switchboard registry's `registered_at` while checking `last_seen_at`/heartbeat age for liveness freshness. The UI should format the row as a human-readable liveness or registration duration and degrade gracefully when timestamps are missing.

## Risks / Trade-offs

- **Shared-container topology** -> In compose, all butlers run under the same `butlers-up` service, so multiple butler cards may show the same `container_name`. That is accurate for the current deployment and more useful than fake per-butler process identifiers.
- **Registry timestamp ambiguity** -> `registered_at` is a registry-row timestamp, not necessarily a kernel process start timestamp. The UI copy should present it as liveness or registration duration, not literal kernel process uptime, and tolerate null when the source is absent.
- **Non-compose deployments** -> `BUTLERS_HOST` may be unset and resolve to `localhost`. The backend should fall back cleanly rather than inventing a container name.
