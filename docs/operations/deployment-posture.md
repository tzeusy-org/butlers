# Deployment Posture

> **Purpose:** Document the dev vs hardened deployment posture, what each gates, and how to opt in.
> **Audience:** Operators running Butlers in local or private-network deployments.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Grafana Monitoring](grafana-monitoring.md).

## Overview

Butlers supports two deployment postures: **dev** and **hardened**. Posture
controls security-sensitive toggles that need to be convenient for local
iteration but tightened for non-dev deployments.

| Posture | Default? | Who it is for |
|---------|----------|---------------|
| `dev` | Yes (when unset) | Local development; single-developer machines |
| `hardened` | Explicit opt-in | Private-network or shared deployments where anon access is undesirable |

**Default-when-unset is `dev`.** The running stack is never broken by a
posture change unless you explicitly request `hardened`.

## What Posture Gates

### Grafana Anonymous Viewer

| Posture | `GF_AUTH_ANONYMOUS_ENABLED` | Effect |
|---------|-----------------------------|--------|
| `dev` | `true` | http://localhost:3000 opens without login |
| `hardened` | `false` | Login required (username: `admin`, password: from `GF_SECURITY_ADMIN_PASSWORD`) |

Additional toggles may be added here as the hardening cycle progresses. Each
new toggle will follow the same pattern: safe-in-dev default, explicit
opt-in for hardened.

### What Posture Does Not Gate: Runtime-Probe Control

The runtime-probe control keys are **not** posture-gated, and deliberately so. Both postures mount
the same documents the same way — the signing key into Dashboard as a secret, the verifier keyring
into Dashboard and all-butlers as a config — and both fail closed when those documents are absent
or unprovisioned. There is no dev-convenience fallback that lets an unsigned or shared-bearer probe
through, because the convenient version of that path is the one the requirement exists to remove.

The dev-versus-production difference is only *which host files you point at*
(`RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE`, `RUNTIME_PROBE_CONTROL_VERIFIERS_FILE`); a machine that
points at neither runs with the plane closed and model verification reporting entries as
*unavailable*. See [Runtime-Probe Control Keys](runtime-probe-control-keys.md).

## Opting Into Dev Posture

Dev posture is the default. No action required. When running the observability
stack:

```bash
./scripts/compose.sh --observability
# Grafana at http://localhost:3000 (anonymous viewer enabled)
```

To make the posture explicit:

```bash
BUTLERS_POSTURE=dev ./scripts/compose.sh --observability
```

## Opting Into Hardened Posture

Pass `--hardened` to `compose.sh`, or set `BUTLERS_POSTURE=hardened` in the
environment before invoking it:

```bash
# Via flag:
./scripts/compose.sh --observability --hardened

# Via environment variable:
BUTLERS_POSTURE=hardened ./scripts/compose.sh --observability
```

Both forms export `GF_AUTH_ANONYMOUS_ENABLED=false` into the compose
environment, which the Grafana service picks up at startup.

### Changing Grafana Admin Password in Hardened Mode

In hardened posture the anonymous viewer is disabled, so operators must log
in. The default credentials remain `admin` / `admin`. To change the password:

```bash
GF_SECURITY_ADMIN_PASSWORD=<your-password> \
  BUTLERS_POSTURE=hardened \
  ./scripts/compose.sh --observability
```

Or set `GF_SECURITY_ADMIN_PASSWORD` in your `.env.dev` / `.env.prod` file.

## Invoking docker compose Directly

When bypassing `compose.sh` and invoking `docker compose` directly, set
`GF_AUTH_ANONYMOUS_ENABLED` explicitly in the environment — the compose file
defaults to `false` (safe) if the variable is unset:

```bash
# Dev (anon viewer on):
GF_AUTH_ANONYMOUS_ENABLED=true \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile observability up -d

# Hardened (login required):
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile observability up -d
# (GF_AUTH_ANONYMOUS_ENABLED unset → defaults to false)
```

## Related Pages

- [Grafana Monitoring](grafana-monitoring.md) — Observability stack setup and signal flow
- [Docker Deployment](docker-deployment.md) — Full service configuration reference
- [Environment Config](environment-config.md) — `.env.dev` / `.env.prod` reference
