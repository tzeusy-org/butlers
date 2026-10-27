# Environment Configuration

> **Purpose:** Configuration that is neither an environment variable nor a dashboard secret:
> mounted key files, dashboard-store callback credentials, and the per-butler roster layout.
> **Audience:** Operators, developers setting up local or production environments.
> **Prerequisites:** [Docker Deployment](docker-deployment.md).

## Overview

Butlers separates infrastructure configuration from application secrets. Infrastructure settings
are environment variables, all listed in
[Environment Variables](../identity_and_secrets/environment-variables.md). Application secrets are
managed on the dashboard Secrets page and resolved DB-first through
[Credential Store](../data_and_storage/credential-store.md). Service ports are defined once in the
[deployment port map](../../about/lay-and-land/deployment.md#port-assignments). This page covers
what falls outside those three homes.

## Runtime-Probe Control Keys

Two deployment documents sit outside the credential resolution order, because they are mounted
files rather than credentials:

```
RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE   # host path -> /run/secrets/runtime_probe_control_signing_key (Dashboard only)
RUNTIME_PROBE_CONTROL_VERIFIERS_FILE     # host path -> /run/secrets/runtime_probe_control_verifiers (Dashboard + all-butlers)
```

Both variables name a **host path**, not a value; Compose mounts the file. Leave them unset and the
stack still boots on tracked placeholders that every parser rejects, which closes the control plane
instead of half-opening it. There is no environment-variable, database, or Secrets-API fallback for
either document --- `RUNTIME_PROBE_CONTROL_SIGNING_KEY` is a reserved name in the Secrets API and
every mutation of it is refused. See
[Runtime-Probe Control Keys](runtime-probe-control-keys.md).

## Telegram Approval Callback Credentials

Store these Tier-1 values in the Dashboard Secrets page's shared-public credential
store, never in Compose or a connector environment file. The Telegram bot connector
and dashboard API load them independently from that store.

| Key | Purpose |
|-----|---------|
| `APPROVAL_CALLBACK_SECRET` | HMAC secret that binds an inline Telegram button to its pending approval action. |
| `APPROVAL_CALLBACK_CONNECTOR_TOKEN` | Dedicated connector credential for only `GET /api/approvals/{id}` and `POST /api/approvals/{id}/approve|deny`, sent as `X-Butlers-Approval-Callback-Token`. |

Owner authentication (passkey, or `DASHBOARD_API_KEY` in configured-key mode) continues to protect
every other dashboard API route. The callback connector token is not a substitute for it and does
not grant general dashboard access.

## Butler Roster Layout

Each butler's configuration is git-tracked under `roster/{butler}/`:

```
roster/{butler}/
  butler.toml       # Identity, port, schedules, modules
  MANIFESTO.md      # Purpose and value proposition
  CLAUDE.md         # System prompt / personality
  AGENTS.md         # Runtime notes
  api/              # Optional dashboard API routes (auto-discovered)
  .agents/skills/   # Skills available to runtime instances
```

Butlers may also carry `tools/`, `jobs/`, `migrations/`, `modules/`, and `tests/`. Scaffolding a new
butler is covered by the `butlers-development` skill.

## Related Pages

- [Environment Variables](../identity_and_secrets/environment-variables.md) -- the variable reference
- [Docker Deployment](docker-deployment.md) -- Container configuration
- [CLI Runtime Auth](../identity_and_secrets/cli-runtime-auth.md) -- CLI credential setup
