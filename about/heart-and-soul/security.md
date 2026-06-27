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
- **The PostgreSQL instance.** Superuser or migration-role database access
  implies full read/write to all butler schemas (runtime butler connections are
  scoped by `SET ROLE`; see Schema Isolation below). The system relies on
  host-level access controls (filesystem permissions, network binding,
  pg_hba.conf) rather than application-level encryption.

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

## Schema Isolation

The single PostgreSQL database uses per-butler schemas with role-based
least-privilege at runtime. `scripts/init-db.sql` creates one LOGIN role per
butler and grants it access only to its own schema plus the shared `public`
schema (read and write), with read-only access to connector schemas it depends
on. At runtime each butler's connection pool issues `SET ROLE` to that butler's
role (`src/butlers/db.py`), so a butler's queries cannot read or write another
butler's schema even though all schemas live in one database.

**Guarantees:**

- A butler runtime connection scoped by `SET ROLE` can reach only its own schema
  and `public`, plus any explicitly granted connector read schemas.
- Cross-butler data access goes through MCP and the Switchboard, not direct SQL.

**Limitations:**

- The migration role and any superuser connection retain full access to all
  schemas, consistent with the trust model (the owner and whoever holds the
  database credentials are trusted).
- Role enforcement degrades to a warning if the per-butler role cannot be
  verified, unless `strict_role_enforcement` is set, in which case startup fails
  closed.

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

Credentials follow a **three-tier authority model**. Each credential has exactly
one authoritative storage location:

### Tier 0: Bootstrap (Environment Variables)

Infrastructure credentials required before the database is available:
`POSTGRES_HOST/PORT/USER/PASSWORD`, `SWITCHBOARD_MCP_URL`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, OAuth redirect URIs, and connector
configuration (`CONNECTOR_*` ports, intervals, etc.).

### Tier 1: System (butler_secrets)

Ecosystem-wide credentials not bound to a specific user identity. Managed via
the **System tab** on the dashboard `/secrets` page. Examples:
`BUTLER_TELEGRAM_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`,
`BLOB_S3_*`, LLM API keys (`cli-auth/*`), `owntracks_webhook_token`.

Accessed at runtime via `CredentialStore.resolve()` or `CredentialStore.load()`.
`CredentialStore.resolve()` is the **canonical implementation of Tier 1 credential-fallback
semantics**: it queries the local `butler_secrets` table first, then any configured fallback
pools, and only reads `os.environ` when `env_fallback=True` is explicitly passed (disabled
by default). See `src/butlers/credential_store.py → CredentialStore.resolve()` for the
authoritative docstring.

### Tier 2: User (entity_info on owner entity)

Identity-bound credentials tied to the owner's personal accounts. Managed via
the **User tab** on the dashboard `/secrets` page. Examples:
`home_assistant_token/url`, `telegram_api_id/hash/session`,
`email/email_password`, `whatsapp_phone`. Per-account credentials (Google OAuth
refresh tokens, Steam API keys) live on companion entities.

Accessed at runtime via `resolve_owner_entity_info(pool, info_type)` for owner
credentials, or direct SQL on companion entity UUIDs for per-account tokens.

### Resolution Rules

- Env vars are NOT automatic overrides. For Tier 1, `CredentialStore.resolve()` only
  reads `os.environ` when `env_fallback=True` is explicitly passed (disabled by default).
  Tier 0 is the only tier where `os.environ` is the authoritative source.
- When a connector needs a credential, it MUST read from the authoritative tier.
- New credentials MUST be classified into a tier when added.

### Constraints

- Credentials must never appear in git-tracked configuration files.
- Credentials must never appear in session logs or tool call payloads sent to
  the dashboard.
- The credential store uses the database's access controls, not application-level
  encryption. This is consistent with the trust model: if the database is
  compromised, the attacker already has access to the data the credentials
  protect.
- Tier 1 connectors MUST use `CredentialStore.resolve()` for secret access.
- Tier 2 connectors MUST use `resolve_owner_entity_info()` --- never
  `CredentialStore` for identity-bound credentials.
- Direct `os.environ.get()` for API keys or tokens is forbidden outside Tier 0.

## Identity Resolution

Identity resolution maps channel-specific identifiers (Telegram chat ID, email
address, Discord user ID) to a known entity and their roles. The canonical
reverse-lookup (`resolve_contact_by_channel` in `src/butlers/identity.py`) reads
`relationship.entity_facts` RDF triples (predicates `has-handle`, `has-email`,
`has-phone`) joined to `public.entities`. Under the seam law (RFC 0004
Amendment 3), `relationship.entity_facts` is the single source of truth for all
non-secret identifiers and relationships, while `public.entity_info` holds only
secured credentials. Telegram handles are stored canonically with a `telegram:`
prefix. This enables:

- **Sender recognition:** Knowing who sent a message regardless of channel.
- **Cross-channel context:** The relationship butler knows that the person who
  emailed is the same person who messaged on Telegram.
- **Owner identification:** The owner entity (`'owner' = ANY(roles)`) is
  recognized across all channels and is the resolution target for owner-directed
  delivery.

**Limitations:**

- Identity resolution is not cryptographic authentication. A Telegram chat ID
  can be associated with an entity, but the system cannot prove the person
  controlling that Telegram account is who they claim to be.
- Entity merging (deduplication) is a manual or LLM-assisted process, not
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

Four Docker bridge networks separate connectivity by function:

| Network | Purpose |
|---------|---------|
| `db` | Database and storage (postgres, minio). |
| `backend` | Inter-service communication (butlers, switchboard, connectors). |
| `frontend` | Vite dev server to dashboard-api only. |
| `egress` | Outbound internet for services that call external APIs (LLM providers, Google OAuth, Telegram, Gmail). |

Services that need external API access join the `egress` network in addition to
their functional networks.

None of these networks set Docker's `internal: true` flag. Docker's
`DOCKER-ISOLATION-STAGE-1` rules for multiple internal networks interfere with
each other and break inter-container communication, so isolation is enforced by
the egress firewall (`scripts/egress-firewall.sh`) using per-bridge iptables
rules on the `DOCKER-USER` chain rather than by the `internal` flag. The firewall
is what blocks a compromised container from reaching private subnets; see the
next section for its allow and deny rules.

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
credential store. `HOME` is NOT included by default; adapters that need it
(e.g. CodexAdapter) set it explicitly per invocation, pointing to an isolated
temp directory containing session-specific config.

### Persistent Runtime State

LLM runtime CLIs (codex, opencode, claude-code, gemini) store OAuth tokens and
settings in their config directories (`~/.codex/`, `~/.claude/`, etc.). These
are backed by named Docker volumes so tokens survive container restarts without
requiring re-authentication.

Note: The CodexAdapter overrides `HOME` to a per-invocation temp directory for
MCP config discovery. The persistent `runtime_codex` volume at `/root/.codex`
stores auth tokens used by the container-level `codex` binary, but session MCP
config is always ephemeral and written to the temp directory.

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
