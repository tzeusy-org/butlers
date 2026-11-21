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
bws run -- scripts/k8s/public-mcp-smoke.sh dev         # dark smoke + witness, throwaway test key
```

## Runtime contract

| | |
|---|---|
| Image, command | the `butlers-app` image at the butlers TAG; `python -m butlers.mcp_public.server` (the `butlers-public-mcp` console script's target) through the image's sealed `python_entrypoint.py`, because the image puts no venv on PATH |
| Port | `8080` (path `/mcp`), TCP liveness and readiness; metrics port `9090` (scraped by `lgtm`) |
| Env | `MCP_PUBLIC_DB_HOST` (`postgres-<env>-rw`), `_DB_PORT=5432`, `_DB_NAME=butlers_public`, `_DB_USER=mcp_public_ro`, `MCP_PUBLIC_DASHBOARD_BASE`, `MCP_PUBLIC_AUDIENCE` and `MCP_PUBLIC_ALLOWED_SUBS` (required, site values), optional `MCP_PUBLIC_CLIENT_DOMAINS` and `MCP_PUBLIC_REGISTERED_CLIENTS`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. No secret is an env var |
| Files (read-only directories) | `bearer/digest` (+ optional `digest_next`), `db/password`, `canaries/canaries.json` (Secrets); `keyring/keyring.json`, `switches/disabled_domains` (ConfigMaps), all under `/etc/butlers-public-mcp/` |
| Pod | one replica, `Recreate`, non-root (uid 10001), read-only root filesystem, `/tmp` emptyDir only, no service-account token, drop ALL, seccomp RuntimeDefault |

## Secrets (ExternalSecrets, dev Bitwarden store, no value in the chart)

| Secret | Bitwarden item | File | Holds |
|---|---|---|---|
| `butlers-public-mcp-db` | `MCP_PUBLIC_RO_DB_PASSWORD` | `db/password` | the `mcp_public_ro` password. Only this chart references it (`tests/scripts/test_helm_butlers_public_mcp.py` renders the butlers chart to prove it) |
| `butlers-public-mcp-bearer` | `MCP_PUBLIC_HOP_BEARER_DIGEST` (+ `bearerDigestNextKey`) | `bearer/digest`, `bearer/digest_next` | lowercase hex SHA-256 of the filter-proxy `UPSTREAM_BEARER`, never the bearer |
| `butlers-public-mcp-canaries` | `MCP_PUBLIC_CANARIES` | `canaries/canaries.json` | JSON list of canary strings, at least 8 characters each. The list must be non-empty: a missing, invalid or empty (`[]`) file makes every tool refuse. The chart cannot read the item, so this is a documented rule checked by the live checks |

## Network policy (default deny both ways, four allows)

| | Peer | Port |
|---|---|---|
| ingress | pods labelled `app.kubernetes.io/instance=mcp-butlers` in namespace `mcp` (homelab filter-proxy release `butlers`; `helm/mcp_server` prefixes `mcp-`) | 8080 |
| ingress | namespace `lgtm` (monitoring) | 9090 |
| egress | `postgres-<env>` instance pods (`cnpg.io/cluster`, `cnpg.io/podRole=instance`) | 5432 |
| egress | kube-dns | 53 UDP/TCP |

No internet egress. Nothing is added on the `postgres-<env>` side: no policy selects those pods
on dev, and a new one would cut off the primary's other clients (D8 item 3).
`butlers-public-mcp` has a row in `deploy/egress-profiles.toml` (database 5432, no external hosts).

## Values

`values.yaml` holds defaults and requires `env`. The network allows, ports (8080, 9090), `postgres-<env>` names and the single replica are constants: a value that tries to set them (`networkPolicy`, `ports`, `database.host|port|clusterNamespace|clusterName`, `replicas` other than 1) fails the render; `values.dev.yaml` sets dev. There is no prod values
file (OD18). `audience` and `allowedSubs` are required and never committed: the deploy script passes
them from `BUTLERS_PUBLIC_MCP_AUDIENCE` and `BUTLERS_PUBLIC_MCP_ALLOWED_SUBS` (env or the site env
file). `keyringJson` is the verifier keyring document, rendered verbatim from the file named by
`BUTLERS_PUBLIC_MCP_KEYRING_FILE` and empty until owner act 4 (the server then verifies nothing and
refuses). `disabledDomains` is the break-glass file (one domain or `all` per line, `#`
comments; default a comment only; edit with `kubectl` or Helm, re-read every 10 s). Image registry
comes from `BUTLERS_IMAGE_REGISTRY`, `dashboardBase` from `BUTLERS_PUBLIC_HOST`.

## Release placement

Helm cannot store a release in a namespace the chart creates, so the release `butlers-public-mcp`
is recorded in `butlers-dev` while every object carries `namespace: butlers-public-dev`. The
Namespace has `helm.sh/resource-policy: keep`.

## Alerts and flags (RFC 0039 D16, task 3.2)

`templates/podmonitor.yaml` scrapes the server's metrics port (`GET :9090/metrics`, job
`butlers-public-mcp`) and `templates/prometheusrule.yaml` holds every alert condition plus the two
flag recording rules. Both are `monitoring.podMonitor.enabled` / `monitoring.rules.enabled` (off in
`values.yaml`, on in `values.dev.yaml`) and carry `monitoring.*.labels` (dev: `release: lgtm`, what
the cluster's kube-prometheus-stack selects). Nothing is added to ingress: the NetworkPolicy
already admits `lgtm` on 9090. Alerts never go through Butlers; the owner's Uptime Kuma polls the
flags (`docs/operations/public-mcp-read-surface.md`, 3e).

The flags are `butlers_public_mcp:critical_ok` and `butlers_public_mcp:warning_ok`: 1 while no alert
of that severity fires, 0 while any does, always one series (never empty while Prometheus
evaluates). They read `ALERTS{alertstate="firing", severity=..., surface="butlers-public-mcp"}`, so
unrelated cluster alerts cannot move them. Every alert carries `severity` and `surface`, plus its
diagnostic labels (`domain`, `client`, `slot_name`, `tool`, `change`) and never row content.

| Alert | Severity | Reads | Fires when |
|---|---|---|---|
| `PublicMcpCanaryHit` | critical | `butlers_public_mcp_canary_hits_total` | a hit in the last `eventWindow` |
| `PublicMcpCanaryLatched` | critical | `butlers_public_mcp_canary_latched` | the latch holds (until pod restart) |
| `PublicMcpSlotRetainedWal` | critical | `cnpg_pg_replication_slots_pg_wal_lsn_diff` | a slot retains over `slotRetainedWalBytes` |
| `PublicMcpForeignLogicalSlot` | critical | `cnpg_pg_replication_slots_active` | a logical slot other than `butlers_public_sub` or `pg_<oid>_sync_*` exists |
| `PublicMcpNarrowingOwnerStepPending` | critical | `butlers_public_mcp_owner_step_pending{change="narrowing"}` | 1 for `narrowingPendingFor` (24h) |
| `PublicMcpOwnerStepPending` | warning | `butlers_public_mcp_owner_step_pending` | 1 with either `change` label |
| `PublicMcpDlpDrop` | warning | `butlers_public_mcp_dlp_blocked_total` | any drop in the last `eventWindow` |
| `PublicMcpRedactionsHigh` | warning | `butlers_public_mcp_redactions_total` | over `redactionsPerHour` in 1h |
| `PublicMcpAssertionFailures` | warning | `butlers_public_mcp_auth_failures_total` | over `assertionFailuresPer5m` in 5m |
| `PublicMcpBearerRefusals` | warning | `butlers_public_mcp_bearer_refusals_total` | over `bearerRefusalsPer5m` in 5m |
| `PublicMcpBulkRead` | warning | `butlers_public_mcp_rows_served_total{domain=~"finance\|health"}` | over `rowsServedPerHour` in 1h for one `(domain, client)` |
| `PublicMcpSlotInactive` | warning | `cnpg_pg_replication_slots_active` | a logical slot at 0 for `slotInactiveFor` (10m) |
| `PublicMcpInboxUndrained` | warning | `butlers_public_mcp_proposal_import_inbox_depth` | over `inboxUndrained` |
| `PublicMcpProposalApplyFailed` | warning | `butlers_public_mcp_proposal_import_outcomes_total{state="apply_failed"}` | any transition in the last `eventWindow` |

Every threshold is a value under `monitoring.rules.thresholds`. `slotRetainedWalBytes` is 50 % of
the cluster's `max_slot_wal_keep_size` (`10GB` on dev, so `5368709120`) and has no metric to derive
it from: keep the two in step.

**Sources that live outside this chart.** The `cnpg_*` series come from CNPG's default exporter
queries on `postgres-<env>` and exist in the cluster Prometheus only if the homelab scrapes the
instance pods' exporter port (the dev `Cluster` has `monitoring.enablePodMonitor: false`, so the
scrape is the homelab's, not verified here). The importer series are exported by the butlers
daemons over OTLP (OTel meter `butlers`, names with dots turned into underscores and `_total` on
counters, as the repo's Grafana dashboards read them) and need no scrape from this chart.

**Gaps, no metric source today.** These conditions from REQ-public-mcp-authorization-014 have no
rule, rather than a rule on an invented series:

- Replication lag over 10 minutes (the `as_of` age): the server reads `as_of` per request and
  exports no gauge, and a gauge refreshed only by requests would alarm on an idle server. Needs a
  background poll in the server or a CNPG custom query on `pg_stat_subscription`.
- A logical slot whose plugin is not `pgoutput`, on a name the allowance admits: CNPG's default
  slot query has no `plugin` column. A foreign slot name is covered; a `butlers_public_sub` slot
  created with another plugin is not. Needs a custom query adding `plugin`.
- One `(sub, cid)` reading over 25 % of a domain's published rows in a day: `rows_served_total`
  carries the client label but not the subject, and no metric holds a domain's published row count.

Also by design: the owner-step gauge is refreshed by requests, so a pod restart clears it until the
next call and restarts the 24-hour clock; the table-sync slot allowance matches the name pattern,
not the subscription's current oid.

`tests/scripts/test_public_mcp_alert_rules.py` renders the rules and runs `promtool test rules`
with synthetic series, one case per condition that must drive a flag to 0 (the pinned promtool
comes from `make install-lint-tools`; CI's guards job requires it). The live Uptime Kuma fire drill
(force each flag to 0, watch the monitor go DOWN and Telegram arrive) is owner act 7 and is not
done.
