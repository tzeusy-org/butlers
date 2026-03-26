# Security Model

## Trust Premise

Butlers is user-federated. One user. One instance. The user owns the machine,
the database, the credentials, and the LLM API keys. There is no multi-tenant
isolation because there is no multi-tenancy.

This premise shapes every security decision. Threats that matter in SaaS
architectures --- cross-tenant data leakage, privilege escalation between users,
shared secret management --- do not apply here. The threat model is simpler:
protect the owner's data from unauthorized access and prevent the system's agents
from taking actions beyond their intended scope.

## Threat Boundaries

### What is trusted

- **The owner.** The person who deployed the instance has full access to
  everything: database, filesystem, credentials, API keys. There is no access
  control within the system that restricts the owner.
- **The host machine.** If the machine is compromised, the system is compromised.
  Butlers does not attempt to defend against a hostile operating environment.
- **The PostgreSQL instance.** Database access implies full read/write to all
  butler schemas. The system relies on host-level access controls (filesystem
  permissions, network binding, pg_hba.conf) rather than application-level
  encryption.

### What is partially trusted

- **LLM API providers.** Prompts and tool call payloads are sent to external LLM
  APIs (Anthropic, Google, OpenAI). The owner accepts this as a condition of
  using the system. Sensitive data in prompts is exposed to the provider's
  infrastructure. The system does not attempt to redact or encrypt prompt
  content. This includes data from all connectors: location coordinates
  (OwnTracks), listening history (Spotify), smart home state (Home Assistant),
  and file metadata (Google Drive) may appear in LLM prompts when butlers
  process ingested events from these sources.
- **Ephemeral LLM sessions.** Each session is sandboxed to its own butler's MCP
  tools. A health butler session cannot call finance butler tools. However, the
  LLM itself may attempt unexpected tool call patterns, which is why approval
  gates exist for sensitive operations.

### What is untrusted

- **External message senders.** Messages arriving through connectors (Telegram,
  Gmail, Discord) come from potentially unknown or impersonated senders. The
  identity resolution system maps sender identifiers to canonical contacts, but
  does not authenticate them cryptographically.
- **Connector transport.** Messages in transit between external services and
  connectors are subject to the transport's own security model (Telegram's
  encryption, Gmail's TLS, etc.). Butlers does not add an additional encryption
  layer.

## Session Sandboxing

When a butler spawns an ephemeral LLM session, the session receives a
locked-down MCP configuration containing only that butler's registered tools.

**Guarantees:**

- A session for the health butler cannot call finance butler tools.
- A session cannot access the Switchboard's routing tools.
- A session cannot modify its own MCP configuration at runtime.
- A session cannot spawn other sessions.

**Limitations:**

- The LLM may hallucinate tool names or attempt calls that do not exist. The MCP
  server rejects these, but the attempt consumes tokens and session time.
- The LLM has access to all tools registered by the butler's modules. There is
  no per-session tool restriction within a single butler. If a module's tool is
  loaded, every session of that butler can call it.

## Approval Gates

The approvals module provides safety gates for sensitive tool calls. When a tool
is marked as requiring approval, the system pauses execution and requests
explicit owner confirmation before proceeding.

**Use cases:**

- Sending messages on behalf of the owner (email, Telegram).
- Modifying calendar events.
- Deleting data.
- Any action with real-world consequences that cannot be undone.

**Design constraints:**

- Approval gates must never be bypassable by the LLM session. The gate is
  enforced at the MCP server level, not in the prompt.
- Approval timeouts must result in denial, not silent approval.
- The approval mechanism must work across all notification channels (dashboard,
  Telegram, etc.).

## Credential Management

Secrets follow a DB-first resolution model. The `CredentialStore` checks the
`butler_secrets` database table first, falling back to environment variables
only when the DB has no value. This means:

- **All runtime secrets** (API keys, OAuth tokens, integration credentials) are
  stored in the database and managed via the dashboard Secrets page. No secret
  env vars are required for normal operation.
- **Environment variables** are reserved for infrastructure bootstrap only:
  database connection parameters (`POSTGRES_HOST/PORT/USER/PASSWORD`) and
  optional observability (`OTEL_EXPORTER_OTLP_ENDPOINT`).
- **Dashboard OAuth flow** handles interactive credential setup (Google OAuth,
  Spotify PKCE, etc.). Tokens are stored in the DB after the flow completes.
- **Bearer tokens** for webhook-based connectors (OwnTracks) are generated and
  stored via the dashboard, validated on every incoming request.

**Constraints:**

- Credentials must never appear in git-tracked configuration files.
- Credentials must never appear in session logs or tool call payloads sent to
  the dashboard.
- The credential store uses the database's access controls, not application-level
  encryption. This is consistent with the trust model: if the database is
  compromised, the attacker already has access to the data the credentials
  protect.
- Connectors and butlers MUST use `CredentialStore.resolve()` for secret
  access --- never direct `os.environ.get()` for API keys or tokens.

## Identity Resolution

The shared contacts system maps channel-specific identifiers (Telegram chat ID,
email address, Discord user ID) to canonical contacts. This enables:

- **Sender recognition:** Knowing who sent a message regardless of channel.
- **Cross-channel context:** The relationship butler knows that the person who
  emailed is the same person who messaged on Telegram.
- **Owner identification:** The owner's contact is bootstrapped at startup and
  recognized across all channels.

**Limitations:**

- Identity resolution is not cryptographic authentication. A Telegram chat ID
  can be associated with a contact, but the system cannot prove the person
  controlling that Telegram account is who they claim to be.
- Contact merging (deduplication) is a manual or LLM-assisted process, not
  automatic.

## Sensitive Data Categories

Some connectors ingest data that is more privacy-sensitive than typical messages:

- **Location data** (OwnTracks): GPS coordinates and geofence transitions.
  Default ingestion tier is `metadata` (no raw coordinates in the ingest
  payload). Configurable retention with a conservative default (30 days).
  The connector is opt-in only.
- **Listening history** (Spotify): Tracks, playlists, and playback sessions.
  Low-sensitivity individually, but aggregated patterns reveal daily routines.
- **Smart home state** (Home Assistant): Device states, automation triggers,
  sensor readings. Reveals presence, habits, and home occupancy patterns.

These data types are governed by the same trust model as all other data: the
user owns the instance and the database. The system does not apply differential
privacy, anonymization, or special-purpose encryption to any data category.
Connector-level controls (ingestion tier, retention periods, opt-in activation)
are the primary privacy mechanism.

## Why Encryption at Rest Adds Minimal Value

In a user-federated system where the user owns the database:

- The encryption key would be stored on the same machine as the database. An
  attacker with filesystem access has both.
- Application-level encryption prevents the database from indexing encrypted
  fields, breaking search, aggregation, and the JSONB query patterns the system
  relies on.
- PostgreSQL's native encryption (TDE) or filesystem encryption (LUKS, FileVault)
  provide the same protection with less application complexity.

If the owner wants encryption at rest, they should enable it at the filesystem
or database level, not at the application level. Butlers does not re-implement
what the storage layer already provides.

## Deployment Security

Butlers runs in Docker Compose with a layered network isolation model.
The principle: **services get the minimum network access they need and
nothing more.**

### Network Isolation Model

Four Docker networks enforce least-privilege connectivity:

| Network | `internal` | Purpose |
|---------|-----------|---------|
| `db` | yes | Database and storage. No outbound internet. |
| `backend` | yes | Inter-service communication (butlers, switchboard, connectors). No outbound internet. |
| `frontend` | yes | Vite dev server to dashboard-api only. No outbound internet. |
| `egress` | no | Outbound internet for services that call external APIs (LLM providers, Google OAuth, Telegram, Gmail). |

Services that need external API access join the `egress` network in addition to
their functional networks. Services that don't (postgres, minio, migrations,
log-init, oauth-gate) are restricted to internal networks and cannot reach the
internet at all.

### Private Subnet Firewall

The `egress` network allows internet access but blocks private subnets by
default using iptables rules on the `DOCKER-USER` chain:

- **Blocked:** RFC1918 (LAN), Tailscale CGNAT (100.64.0.0/10), link-local.
- **Allowed:** Specific tailnet hosts listed in `ALLOWED_TAILNET_HOSTS`
  (resolved dynamically by `compose.sh` at startup).

This prevents a compromised container from pivoting to LAN machines, SSH'ing
into other hosts, or scanning the tailnet --- while still allowing the specific
tailnet services Butlers depends on (OTEL collector, Garage S3, Ollama, external
Postgres).

### Host Port Binding

All port mappings bind to `127.0.0.1` only. No service is accessible from the
LAN or tailnet via its Docker port. External access (when needed) is handled
by Tailscale serve, which provides HTTPS termination and tailnet-level
authentication.

### Container Environment Isolation

Docker containers receive a clean environment. Host environment variables (API
keys, SSH credentials, cloud provider tokens) do not leak into containers.
Only variables explicitly declared in `environment:` or `env_file:` are visible.

The spawner's `_build_env()` is even more restrictive: LLM runtime subprocesses
receive only `PATH` plus explicitly declared credentials resolved from the
credential store.

### Persistent Runtime State

LLM runtime CLIs (codex, opencode, claude-code, gemini) store OAuth tokens and
settings in their config directories (`~/.codex/`, `~/.claude/`, etc.). These
are backed by named Docker volumes so tokens survive container restarts without
requiring re-authentication.

### Principles

1. **Bind to localhost unless there is a specific reason not to.** Tailscale
   serve handles external HTTPS exposure.
2. **Internal by default, egress by exception.** A service should not join the
   `egress` network unless it calls external APIs.
3. **Allowlist, not blocklist, for private network access.** The firewall blocks
   all private subnets then punches holes for specific tailnet hosts.
4. **No secrets in compose files.** Infrastructure bootstrap vars only. All
   runtime secrets are DB-first via `CredentialStore`.
5. **No `privileged: true`, no Docker socket mounts, no `cap_add`.** Containers
   run with default Docker capabilities.

## Anti-Patterns

- Adding application-level encryption that duplicates filesystem/database
  encryption.
- Building multi-tenant access controls for a single-user system.
- Trusting LLM sessions to self-enforce security boundaries (use MCP tool
  restrictions and approval gates instead).
- Storing credentials in `butler.toml`, `CLAUDE.md`, or any git-tracked file.
- Assuming sender identity based solely on channel identifiers without
  confirmation for high-stakes actions.
- Logging full credential values in session logs or error messages.
- Binding Docker ports to `0.0.0.0` (exposes services to the entire LAN).
- Adding services to the `egress` network when they don't call external APIs.
- Hardcoding tailnet IPs in compose files (use `ALLOWED_TAILNET_HOSTS` and
  dynamic resolution instead).
- Using `os.environ.get()` directly for API keys or tokens in connector or
  butler code (use `CredentialStore.resolve()` instead).
