## Context

Health publishes `butler.control.v1` identity facts on its existing port. Its
`accepting_routes` flag requires boot registration, an active server and an
accepting spawner. This is stronger evidence than the process `/health` endpoint.

## Decisions

Use a Google Health-only connector value to render an init container after the
existing OAuth and Switchboard gates. Probe the fixed Health service endpoint
`http://butlers-up:41103/internal/control-plane/identity`; the Switchboard service
continues to follow the existing `butlersControl.enabled` topology switch.

The standalone Python script uses the existing async HTTP client with proxies
and redirects disabled. Require HTTP 200, a JSON object, schema version
`butler.control.v1`, `butler_name=health`, an exact integer positive `boot_epoch`
and literal boolean `accepting_routes=true`. It grants no observation, registry
or route authority and performs no writes. Bound responses at 2 KiB, each whole
request at five seconds or the remaining budget, and the complete polling
operation at 300 seconds by default. Poll every five seconds. Fixed diagnostic
messages contain no endpoint, payload or exception content. Deadline expiration
exits nonzero so Kubernetes can retry the init container.

## Scope and Rollback

This is a frozen-path correctness fix, not fleet readiness or delivery recovery.
The HTTP observation is not an atomic guarantee about a later delivery. Runtime
account discovery, OAuth, cursors and accepted ingestion remain unchanged.
Rollback reverts the chart and probe together; it requires no data repair.

## Verification

Exercise the executable probe against a local HTTP server: ready, unavailable
then ready, unready/malformed identity, oversized data, slow responses and finite
deadline. Render the actual chart with the control split enabled and disabled;
verify gate order, fixed Health target and all other connector init containers.
