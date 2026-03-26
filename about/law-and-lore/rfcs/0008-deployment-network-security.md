# RFC 0008: Deployment Network Security

**Status:** Accepted
**Date:** 2026-03-25

## Summary

Butlers runs in Docker Compose with four named networks (`db`, `backend`, `frontend`, `egress`) that enforce least-privilege connectivity. Three internal networks isolate services that have no business reaching the internet. One egress network provides outbound access for services that call external APIs. An iptables-based firewall on the egress network blocks access to private subnets (LAN, Tailscale) except for explicitly allowed tailnet hosts. All host port bindings use `127.0.0.1` to prevent LAN exposure.

## Motivation

Butlers manages personal data (contacts, health records, email content, OAuth tokens) and spawns LLM CLI subprocesses that have full network access within their container. A compromised connector or LLM session could pivot to the owner's LAN, SSH into other machines, exfiltrate data to the tailnet, or scan for vulnerable services. The user-federated trust model (see `security.md`) trusts the host machine but not necessarily every process running inside it --- especially ephemeral LLM sessions driven by probabilistic models.

## Design

### Network Topology

Four Docker Compose networks with explicit isolation boundaries:

```
                     ┌──────────────────────────────────────┐
                     │          PUBLIC INTERNET              │
                     │  (Anthropic, OpenAI, Google, Telegram)│
                     └──────────────┬───────────────────────┘
                                    │
                    ┌───────────────┤ egress (bridge)
                    │               │
                    │  butlers-up, connectors (4),
                    │  dashboard-api, butler daemons (4)
                    │               │
                    ╔═══════════════╧════════════════╗
                    ║  BLOCKED by iptables:          ║
                    ║    192.168.0.0/16  (LAN)       ║
                    ║    10.0.0.0/8      (RFC1918)   ║
                    ║    100.64.0.0/10   (Tailscale) ║
                    ║    172.16.0.0/12   (RFC1918)   ║
                    ║    169.254.0.0/16  (link-local)║
                    ║                                ║
                    ║  EXCEPT allowed tailnet hosts:  ║
                    ║    otel, butlers-db-dev,        ║
                    ║    ollama, tzehouse-synology     ║
                    ╚════════════════════════════════╝

  ┌─────────────────────────────────────────────────────────┐
  │            INTERNAL ONLY (no internet)                   │
  │  db:       postgres, minio, migrations, oauth-gate      │
  │  backend:  (shared with egress services for inter-svc)  │
  │  frontend: frontend-dev ↔ dashboard-api only            │
  └─────────────────────────────────────────────────────────┘
```

### Network Definitions

| Network | Driver | `internal` | Services |
|---------|--------|-----------|----------|
| `db` | bridge | `true` | postgres, minio, migrations, oauth-gate, all butlers, all connectors, dashboard-api |
| `backend` | bridge | `true` | switchboard, butlers, connectors, dashboard-api, butlers-up |
| `frontend` | bridge | `true` | frontend-dev, dashboard-api |
| `egress` | bridge | `false` | butlers-up, all connectors, dashboard-api, switchboard, general, relationship, health |

A service on an `internal: true` network cannot reach any address outside Docker's bridge networks. A service MUST also join the `egress` network to reach the internet.

### Per-Service Network Assignment

| Service | db | backend | frontend | egress | Why egress? |
|---------|:--:|:-------:|:--------:|:------:|-------------|
| postgres | x | | | | Database only |
| minio | x | | | | Object storage only |
| migrations | x | | | | DB migration only |
| oauth-gate | x | | | | DB poll only |
| log-init | | | | | Filesystem only |
| frontend-dev | | | x | | Serves static + proxies to dashboard |
| switchboard | x | x | | x | Spawns LLM CLIs |
| general | x | x | | x | Spawns LLM CLIs, modules call external APIs |
| relationship | x | x | | x | Spawns LLM CLIs, modules call external APIs |
| health | x | x | | x | Spawns LLM CLIs, modules call external APIs |
| dashboard-api | x | x | x | x | Google OAuth flow |
| butlers-up | x | x | | x | Spawns LLM CLIs for all butlers |
| connector-telegram-bot | x | x | | x | Telegram API |
| connector-telegram-user | x | x | | x | Telegram MTProto |
| connector-gmail | x | x | | x | Gmail API |
| connector-live-listener | x | x | | x | Audio processing APIs |

### Egress Firewall

The `scripts/egress-firewall.sh` script manages iptables rules on the `DOCKER-USER` chain, which Docker evaluates before its own forwarding rules.

**Rule structure (insertion order matters):**

```
1. ACCEPT  -i <egress-bridge> -d <allowed-host-1>     # Specific tailnet hosts
2. ACCEPT  -i <egress-bridge> -d <allowed-host-2>
   ...
N. DROP    -i <egress-bridge> -d 10.0.0.0/8            # RFC1918 Class A
   DROP    -i <egress-bridge> -d 172.16.0.0/12         # RFC1918 Class B
   DROP    -i <egress-bridge> -d 192.168.0.0/16        # RFC1918 Class C (LAN)
   DROP    -i <egress-bridge> -d 100.64.0.0/10         # CGNAT / Tailscale
   DROP    -i <egress-bridge> -d 169.254.0.0/16        # Link-local
```

ACCEPT rules are inserted before DROP rules (using `iptables -I` for ACCEPTs, `iptables -A` for DROPs). This creates a whitelist: only explicitly listed tailnet hosts are reachable; all other private addresses are blocked.

**Bridge interface resolution:** The script resolves the Docker bridge interface for the `egress` network via `docker network inspect`, mapping the network ID to a `br-<hash>` interface. Rules are scoped to traffic entering through this interface only.

**Marker comments:** All rules are tagged with `butlers-egress-allow` or `butlers-egress-firewall` comments for idempotent re-application and clean removal via `--remove`.

### Tailnet Host Allowlist

`compose.sh` dynamically resolves tailnet IPs at startup:

```bash
TAILNET_SERVICES=(
  otel               # OpenTelemetry collector
  butlers-db-dev     # External PostgreSQL
  ollama             # Local LLM inference
  tzehouse-synology  # Garage S3 storage
)
```

Resolution uses `tailscale status --json` with DNS name matching (`<host>.<tailnet-domain>.`), which is stable across Tailscale IP reassignments. Resolved IPs are passed to `egress-firewall.sh` via the `ALLOWED_TAILNET_HOSTS` environment variable.

The allowlist can be overridden in `.env`:

```
ALLOWED_TAILNET_HOSTS=100.67.230.39 100.105.147.86 100.91.37.56 100.66.12.51
```

### Host Port Binding

All `ports:` mappings in compose files MUST use the `127.0.0.1:` prefix:

```yaml
ports:
  - "127.0.0.1:41200:41200"   # correct
  - "41200:41200"              # WRONG — binds to 0.0.0.0
```

Docker's default behavior binds to all interfaces (`0.0.0.0`), which exposes the service to the entire LAN. Since Butlers uses Tailscale serve for external HTTPS access, no service needs to bind to anything other than localhost.

### Container Environment Isolation

Containers receive only explicitly declared environment variables. Host shell environment does not leak through. The spawner's `_build_env()` further restricts the LLM subprocess environment to `PATH` + declared credentials from `CredentialStore`.

### Persistent Runtime State

LLM CLI config directories are backed by named Docker volumes:

| Volume | Mount path | Contents |
|--------|-----------|----------|
| `runtime_claude` | `/root/.claude` | Claude Code OAuth tokens, settings |
| `runtime_codex` | `/root/.codex` | Codex auth tokens, settings |
| `runtime_opencode` | `/root/.opencode` | OpenCode config, auth |
| `runtime_gemini` | `/root/.gemini` | Gemini CLI OAuth tokens |

These volumes persist across container restarts. `HOME=/root` is set explicitly in the container environment so spawned LLM CLI subprocesses find their config directories.

## Invariants

1. Services on internal-only networks (`db`, `backend`, `frontend`) MUST NOT have outbound internet access.
2. The `egress` network MUST have iptables rules blocking all RFC1918 and Tailscale CGNAT subnets except explicitly allowed hosts.
3. All `ports:` mappings MUST bind to `127.0.0.1`.
4. No compose service MUST use `privileged: true`, mount the Docker socket, or use `cap_add`.
5. The `ALLOWED_TAILNET_HOSTS` list MUST be the minimal set of tailnet hosts required for operation.
6. Adding a new tailnet dependency MUST be documented in the `TAILNET_SERVICES` array in `compose.sh` and in this RFC.

## Operational Commands

```bash
# Apply egress firewall (auto-run by compose.sh)
sudo ./scripts/egress-firewall.sh

# Remove egress firewall rules
sudo ./scripts/egress-firewall.sh --remove

# Verify rules are applied
sudo iptables -L DOCKER-USER -n --line-numbers | grep butlers

# Check which networks a service is on
docker compose -f docker-compose.yml -f docker-compose.dev.yml config \
  | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); [print(f'{k}: {list(v.get(\"networks\",{}).keys())}') for k,v in sorted(d['services'].items())]"
```
