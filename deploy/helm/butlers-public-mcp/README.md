# butlers-public-mcp Helm chart

The public MCP read surface (RFC 0039, task 2.4): one deterministic server, no LLM, in its own
namespace `butlers-public-<env>`, reading the projection database `butlers_public` as
`mcp_public_ro`. Setup order and checks: [Public MCP Read
Surface](../../../docs/operations/public-mcp-read-surface.md) (section 3d). The chart is **shipped
dark**: `PUBLIC_MCP_CHART_dev` in `../butlers/makefile` is `off` until the server image exists.

```bash
scripts/k8s/public-mcp-chart-deploy.sh dev <tag> on   # what `make deploy-dev` runs when switched on
make -C deploy/helm/butlers lint                      # helm lint, both charts
scripts/k8s/public-mcp-live-checks.sh dev             # go-live: policy, reachability, isolation
```

## Runtime contract

| | |
|---|---|
| Image, command | the `butlers-app` image at the butlers TAG; `butlers-public-mcp` |
| Port | `8080` (path `/mcp`), TCP liveness and readiness; metrics port `9090` (scraped by `lgtm`) |
| Env | `MCP_PUBLIC_DB_HOST` (`postgres-<env>-rw`), `_DB_PORT=5432`, `_DB_NAME=butlers_public`, `_DB_USER=mcp_public_ro`, `MCP_PUBLIC_DASHBOARD_BASE`, `MCP_PUBLIC_AUDIENCE` and `MCP_PUBLIC_ALLOWED_SUBS` (required, site values), optional `MCP_PUBLIC_CLIENT_DOMAINS` and `MCP_PUBLIC_REGISTERED_CLIENTS`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. No secret is an env var |
| Files (read-only directories) | `bearer/digest` (+ optional `digest_next`), `db/password`, `canaries/canaries.json` (Secrets); `keyring/keyring.json`, `switches/disabled_domains` (ConfigMaps), all under `/etc/butlers-public-mcp/` |
| Pod | one replica, `Recreate`, non-root (uid 10001), read-only root filesystem, `/tmp` emptyDir only, no service-account token, drop ALL, seccomp RuntimeDefault |

## Secrets (ExternalSecrets, dev Bitwarden store, no value in the chart)

| Secret | Bitwarden item | File | Holds |
|---|---|---|---|
| `butlers-public-mcp-db` | `MCP_PUBLIC_RO_DB_PASSWORD` | `db/password` | the `mcp_public_ro` password. Only this chart references it (`tests/scripts/test_helm_butlers_public_mcp.py` renders the butlers chart to prove it) |
| `butlers-public-mcp-bearer` | `MCP_PUBLIC_HOP_BEARER_DIGEST` (+ `bearerDigestNextKey`) | `bearer/digest`, `bearer/digest_next` | lowercase hex SHA-256 of the filter-proxy `UPSTREAM_BEARER`, never the bearer |
| `butlers-public-mcp-canaries` | `MCP_PUBLIC_CANARIES` | `canaries/canaries.json` | JSON list of canary strings, at least 8 characters each; required, or every tool refuses |

## Network policy (default deny both ways, four allows)

| | Peer | Port |
|---|---|---|
| ingress | pods labelled `app.kubernetes.io/instance=butlers` in namespace `mcp` (the filter-proxy release) | 8080 |
| ingress | namespace `lgtm` (monitoring) | 9090 |
| egress | `postgres-<env>` instance pods (`cnpg.io/cluster`, `cnpg.io/podRole=instance`) | 5432 |
| egress | kube-dns | 53 UDP/TCP |

No internet egress. Nothing is added on the `postgres-<env>` side: no policy selects those pods
on dev, and a new one would cut off the primary's other clients (D8 item 3).
`butlers-public-mcp` has a row in `deploy/egress-profiles.toml` (database 5432, no external hosts).

## Values

`values.yaml` holds defaults and requires `env`; `values.dev.yaml` sets dev. There is no prod values
file (OD18). `audience` and `allowedSubs` are required and never committed: the deploy script passes
them from `BUTLERS_PUBLIC_MCP_AUDIENCE` and `BUTLERS_PUBLIC_MCP_ALLOWED_SUBS`. `keyringJson` is the
verifier keyring document, rendered verbatim and empty until owner act 4 (the server then verifies
nothing and refuses). `disabledDomains` is the break-glass file (one domain or `all` per line, `#`
comments; default a comment only; edit with `kubectl` or Helm, re-read every 10 s). Image registry
comes from `BUTLERS_IMAGE_REGISTRY`, `dashboardBase` from `BUTLERS_PUBLIC_HOST`.

## Release placement

Helm cannot store a release in a namespace the chart creates, so the release `butlers-public-mcp`
is recorded in `butlers-dev` while every object carries `namespace: butlers-public-dev`. The
Namespace has `helm.sh/resource-policy: keep`.
