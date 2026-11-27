# Tailnet Health Monitoring

> **Purpose:** Record the repository-proven live dev health route and give an
> authorized operator a bounded Uptime Kuma configuration handoff.
> **Scope:** This is documentation only. It does not authorize a monitor,
> tailnet, TLS, deployment, notification, or runtime change.

## Canonical dev route

The monitored target is the live `butlers-dev` system:

```text
https://<TAILNET_DNS_NAME>/butlers-dev-api/api/health
```

This route is derived from checked-in configuration, not from a live probe:

- The dev Helm values select `publicUrl.apiPrefix: butlers-dev-api` and the
  dashboard NodePort `32200`. The host Tailscale Serve mapping is documented in
  [Kubernetes Deployment](kubernetes-deployment.md#ingress); the chart does not
  configure Tailscale Serve itself.
- The dashboard application exposes `GET /api/health`; Serve strips the public
  mount prefix before the request reaches that route.
- Production has never been deployed and is deferred (OD18). The retained chart
  design uses NodePort `31200` and the route
  `https://<TAILNET_DNS_NAME>/butlers-api/api/health`; retained Compose production
  mode uses that prefix with its loopback-published API port instead of a NodePort.

The owner supplied a content-blind receipt for the off-host dev monitor in
bu-m033r8. This documentation does not verify current host, certificate, Serve,
or monitor state; those are live facts requiring a separately authorized observation.

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
health endpoint of the live `butlers-dev` system:

https://<TAILNET_DNS_NAME>/butlers-dev-api/api/health

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
only sanitized evidence and hand it to the host operator who owns Tailscale
Serve; do not infer a mapping-level repair or attempt a full Serve
reconstruction.
```

## Handoff boundary

The prompt above is intentionally operationally narrow. A failure establishes
only that the required monitor contract was not observed. It does not authorize
changing the route, TLS posture, Serve configuration, host, deployment, or
notification route. Preserve the failure evidence and hand it to the host
operator who owns Tailscale Serve for a separately authorized, diagnosis-first
investigation.

### Probe from off-host

Run the monitor, and any manual check, from a separate tailnet node. On the
Butlers host itself, a request to the host's own tailnet name or IP never
traverses the tailnet: it stays on the local netfilter path, where a
Docker/Traefik DNAT can intercept `:443` and present a default certificate or a
404. That is a false failure; peer traffic arrives over WireGuard on
`tailscale0` and reaches `tailscaled` directly. Confirm any strict-TLS failure
from another node before treating it as a Serve or certificate fault.

## Related sources

- [`values.dev.yaml`](../../deploy/helm/butlers/values.dev.yaml) and
  [`values.prod.yaml`](../../deploy/helm/butlers/values.prod.yaml) — chart prefixes
  and dashboard NodePorts.
- [Kubernetes Deployment](kubernetes-deployment.md#ingress) — current host Serve
  mappings and NetworkPolicy boundary.
- [`scripts/compose.sh`](../../scripts/compose.sh) — retained Compose prefix and
  loopback Tailscale Serve mapping.
- [`src/butlers/api/app.py`](../../src/butlers/api/app.py) — readiness status
  semantics for `GET /api/health`.
- [Deployment network security RFC](../../about/legends-and-lore/rfcs/0008-deployment-network-security.md)
  — loopback-only host port binding and Tailscale HTTPS boundary.
- [Docker Deployment](docker-deployment.md) — Compose launcher and dashboard
  API deployment topology.
