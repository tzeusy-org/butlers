# Tailnet Health Monitoring

> **Purpose:** Record the repository-proven production health route and give an
> authorized operator a bounded Uptime Kuma configuration handoff.
> **Scope:** This is documentation only. It does not authorize a monitor,
> tailnet, TLS, deployment, notification, or runtime change.

## Canonical production route

The only production route covered by this runbook is:

```text
https://<TAILNET_DNS_NAME>/butlers-api/api/health
```

This route is derived from checked-in configuration, not from a live probe:

- Production mode in `scripts/compose.sh` sets `API_PREFIX="butlers-api"`.
- Its Tailscale Serve mapping proxies `/${API_PREFIX}` to the loopback dashboard
  API target `http://localhost:${DASHBOARD_HOST_PORT}`.
- The dashboard application exposes `GET /api/health`; the proxy strips the
  `/butlers-api` mount prefix before the request reaches that route.

The source proof deliberately does not claim that a particular host, certificate,
or running Serve state is currently healthy. Those are live facts requiring a
separately authorized observation.

### Monitor the stack that is actually running

"Canonical" above describes the production *route derivation*, not an instruction
to monitor production on a host where production is not up. `scripts/compose.sh`
selects the prefix by mode, so the dev stack's equivalent route is:

```text
https://<TAILNET_DNS_NAME>/butlers-dev-api/api/health
```

Point the monitor at whichever stack is meant to be always-up on the target host.
Monitoring a stack that is deliberately down produces a permanently failing check,
which trains the operator to ignore the alert and is worse than no monitor at all.
Confirm which stack is running before configuring, and substitute the matching
prefix into the prompt below.

Owner decision 2026-08-16: on the current host the dev stack is the running one,
so the configured monitor targets `/butlers-dev-api/api/health`.

## Health response contract

`GET /api/health` is a readiness check, not an aggregate diagnostic surface.
The monitor must evaluate only these response requirements:

| Application state | Required response |
| --- | --- |
| Lifespan startup has not completed | HTTP 503 with top-level `{"status": "starting"}` |
| Application is ready | HTTP 200 with top-level `{"status": "ok"}` |

For a healthy result, require both HTTP 200 and a parsed top-level JSON
`"status": "ok"`. Do not treat a nested value, a body substring, or unrelated
security-posture fields as a success condition, and do not add aggregate health
diagnostics to this endpoint.

## Pasteable Uptime Kuma agent prompt

```text
Configure exactly one Uptime Kuma HTTP(s) pull monitor for the tailnet-only
health endpoint of the stack that is actually running on the target host
(see "Monitor the stack that is actually running" above):

https://<TAILNET_DNS_NAME>/<API_PREFIX>/api/health

where <API_PREFIX> is butlers-api for the production stack or butlers-dev-api
for the dev stack. Do not configure a monitor against a stack that is down.

Use strict TLS certificate and hostname validation. The monitor must remain
tailnet-only: do not use a public, LAN, or loopback URL and do not weaken TLS
verification. Accept only HTTP 200, and validate the parsed response JSON so
its top-level status field is exactly "ok"; a substring or nested "ok" is not
sufficient.

Set the interval to 60 seconds, timeout to 10 seconds, and use two retries.
Use the existing owner-supplied notification route only. If that route is
unavailable, stop and report it; do not create, edit, or delete a notification
route.

This is a pull check only. Do not configure an event-ingest, webhook, or public
monitor; do not alter Tailscale Serve, certificates, host networking, deployment,
or runtime state.

If TLS validation, hostname validation, or the route/path fails, stop. Capture
only sanitized evidence and route the failure to bu-ln1v7; do not infer a
mapping-level repair or attempt a full Serve reconstruction.
```

## Handoff boundary

The prompt above is intentionally operationally narrow. A failure establishes
only that the required monitor contract was not observed. It does not authorize
changing the route, TLS posture, Serve configuration, host, deployment, or
notification route. Preserve the failure evidence and hand it to `bu-ln1v7` for
the separately authorized diagnosis-first path.

## Related sources

- [`scripts/compose.sh`](../../scripts/compose.sh) — production prefix and
  loopback Tailscale Serve mapping.
- [`src/butlers/api/app.py`](../../src/butlers/api/app.py) — readiness status
  semantics for `GET /api/health`.
- [Deployment network security RFC](../../about/legends-and-lore/rfcs/0008-deployment-network-security.md)
  — loopback-only host port binding and Tailscale HTTPS boundary.
- [Docker Deployment](docker-deployment.md) — Compose launcher and dashboard
  API deployment topology.
