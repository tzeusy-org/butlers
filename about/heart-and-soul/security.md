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
- **The owner's edge and outside AI clients (RFC 0039).** The owner's own
  gateway (Cloudflare Worker OAuth and a GitHub-id allowlist) terminates TLS
  for the public read surface and sees its responses. The owner's outside AI
  clients (ChatGPT and claude.ai) are models outside Butlers: they receive only
  data the projection manifest publishes, and anything they send back is
  third-party content. No request from either carries owner authority.
- **Ephemeral LLM sessions.** Leads and children are partially trusted
  whatever model runs them. Each runs in a sandbox, holds a grant derived from
  its goal's origin, and reaches capabilities only through the broker.
  Approval gates and domain execution checks constrain sensitive operations.

### What is untrusted

- **External message senders.** Messages arriving through connectors (Telegram,
  Gmail, Discord) come from potentially unknown or impersonated senders. The
  identity resolution system maps sender identifiers to canonical contacts, but
  does not authenticate them cryptographically. What a stranger writes may be
  remembered as an attributed report about that sender, but it never becomes
  steering memory: a rule or owner-profile fact that steers later sessions
  requires owner authority, stamped by the server from the goal's origin (never
  from message text, and never judged by an LLM) or explicit owner endorsement.
- **Connector transport.** Messages in transit between external services and
  connectors are subject to the transport's own security model (Telegram's
  encryption, Gmail's TLS, etc.). Butlers does not add an additional encryption
  layer.

## Goal and Delegation Authority

[RFC 0038](../legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
specifies the mechanism. The rules it must satisfy:

- **Authority comes from origin, never from text.** Admission derives a goal's
  ceiling from server-held context: owner, obligation, third party, or
  staffer. A third-party goal can read, record attributed facts, notify the
  owner, and at most prepare a message for owner approval; it can never itself
  send, actuate, or administer.
- **Only the owner is messaged without approval.** Communication with anyone
  other than the owner needs owner approval, per event or through a standing
  rule the owner created, whatever the goal's origin or exposure. This covers
  every party a call delivers to or invites: message recipients, calendar
  attendees and invitees, invitation answers and shares. A party counts as the
  owner only when it resolves uniquely to the live owner entity, as
  `core-notify` defines; any other party, including an unknown, ambiguous or
  undeterminable one, is not the owner.
- **No goal spends.** `transact` capabilities (spend, pay, book) are disabled
  for every goal, including as prepared actions, until the owner enables them
  through an amendment to RFC 0038. A reservation request is a message:
  asking a venue to hold a table or slot through an approval-gated message
  is `communicate`; any step that accepts a fee, deposit, card hold or
  cancellation charge is `book` and stays disabled (owner decision,
  2026-10-20).
- **Grants only narrow.** A lead's grant is at most its goal's ceiling. A
  child's grant is a subset of its lead's, without `administer`. Discovery,
  skills, and task prompts never grant anything.
- **Exposure gates consequential effects.** Once an invocation has read
  third-party content, directly or through records an exposed goal stored,
  sending, administering, and consequential or protected actuation need
  per-event owner approval, and steering-memory writes are refused. Exposure
  never clears, and stored data keeps the taint. The only exemptions are
  `notify` to the owner with link previews off and URLs inert, and standing
  rules whose recipient is the owner or that are template-pinned: every
  content-bearing argument fixed to an exact value or to an owner-authored
  template whose slots take only typed values, never free text.
- **Two independent checks.** The broker checks every call against the grant,
  exposure, approval policy, and budget. The owning domain then verifies the
  broker's single-use assertion for that domain, that exact tool, and that
  argument digest before the handler runs, so a call that bypasses the broker
  is refused even if the sandbox fails.
- **Depth one, enforced by the runtime.** Only a lead holds `goal.delegate`.
  The runner derives depth from lineage; a child cannot reach admission,
  routing, schedule creation, or a new root. Goals admitted as a consequence of
  an effect carry its lineage and exposure, and their causal chains are
  bounded.
- **Owner decisions stay with the owner.** No goal can list or call a tool that
  approves, edits, or dispatches a parked action or mints a standing rule.
- **Stop and recovery are fenced.** Stop revokes every token of the goal in one
  transaction. A replacement or escalated lead advances the goal's epoch, which
  invalidates its predecessor, and inherits the same exposure, budget, and
  effect ledger; running children continue under the new lead. Unknown
  effects are never replayed without fresh owner approval.

Cross-domain retrieval is mediated by the owning services. It preserves
source, sensitivity, and memory-write authority. The lead receives only
authorized context, and children receive the subset their brief needs. Domain
custody is not an excuse to give a lead ambient access to all data. Private
custody content stays unreachable by any model (RFC 0037). Existing approval
and physical-actuation contracts continue to bind.

## Session Sandboxing

The goal runner launches every lead and child on a session host separate from
the domain and control-plane processes, each in its own execution domain.

**Guarantees:**

- A session reaches only the broker's session endpoint and its model provider.
  It cannot reach PostgreSQL, a domain butler's port, or the credential store.
- Runtime built-in shell, filesystem, web, and local-server tools, and
  provider-hosted tools, are disabled. A runtime adapter that cannot prove this
  is ineligible.
- A session authenticates with a single-invocation token that dies with the
  invocation or the goal, and for a lead also with the lead's epoch.
- One invocation cannot read another's token, files, or process state.
- A session cannot modify its own MCP configuration or grant.
- Only the goal runner starts sessions: leads on admission, recovery, and
  escalation, and depth-1 children on a lead's `goal.delegate`.

**Limitations:**

- The model may hallucinate tool names or attempt calls outside its grant. The
  broker rejects these, but the attempt consumes tokens and session time.
- The sandbox still reaches the model provider, which receives prompt content
  under the partial trust described above.

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
  The one guarded exception is the publisher role that feeds the public read
  surface's projection by logical replication. The publication's column lists
  and row filters limit what the projection receives, not what the role can
  read: the publisher role holds `REPLICATION`, which in PostgreSQL can decode
  every change in the database regardless of grants, so it is
  primary-read-all. It therefore has no credential at all: no password or
  certificate exists, it logs in only by local peer authentication inside the
  database instance pods, every network login as it is refused, and no role
  is a member of it. No runtime role, session or internet-facing process can
  act as it. What remains are the permissions that are already
  superuser-equivalent; the publisher adds none of them (see Public Read
  Surface).

**Limitations:**

- The migration role and any superuser connection retain full access to all
  schemas, consistent with the trust model (the owner and whoever holds the
  database credentials are trusted).
- A butler runtime pool must have its designated runtime role before serving queries. Missing or unverifiable roles, inability to assume the role, and a missing role configuration for a butler schema cause startup or connection acquisition to fail closed in every environment. No development or deployment-posture setting permits shared-user runtime continuation.

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
  enforced by the broker and the owning MCP server, not in the prompt.
- An approval executes the exact stored call the owner saw, never a call the
  model re-issues.
- Communication with anyone other than the owner always requires owner
  approval: per event, or through a standing rule the owner created. No goal
  origin, grant or exposure state bypasses it, and no deterministic service
  path bypasses it.
- Exposure to third-party content makes consequential effects approval-gated
  even when the tool alone would not be (see Goal and Delegation Authority).
- Approval timeouts must result in denial, not silent approval.
- The approval mechanism must work across all notification channels (dashboard,
  Telegram, etc.).

Dashboard step-up is required only for standing-rule creation or promotion,
Vouch, Contain, and mode loosening. Ordinary approve, reject, edit and dispatch
remain one-tap owner decisions after their existing authentication, exact-call
and currentness checks; owner-only status alone does not add step-up. Step-up
uses an authenticated dashboard owner session and a single-use nonce bound to
the exact action and current version, consumed atomically with that action
(RFC 0038 D13). The independent passkey factor is on a different device from
the Telegram phone. Telegram identity or a presentation ACK cannot supply that
factor. This contract does not register a factor, change credentials or release
a held Vouch, Contain or mode action.

## Public Read Surface

[RFC 0039](../legends-and-lore/rfcs/0039-public-mcp-read-surface.md) admits
exactly one internet-reachable Butlers workload: a deterministic MCP server
through which the owner's own outside AI clients read Butlers data (owner
decision, 2026-11-04, recorded in
[the decision record](../legends-and-lore/decisions/2026-11-04-doctrine-public-mcp-read-surface.md)). It covers outside clients calling Butlers. It admits no
outside server as a Butlers data source, and the owner's rule that no outside
server gets a write, send or spend effect (`bu-mzvel0`) is unchanged.

**Guarantees.** The exception holds only while all four hold:

- **No credential is reachable.** The server reads only a separate projection
  database that one-way logical replication fills from one checked-in
  allowlist manifest. `butler_secrets`, secured `entity_info` rows, connector
  and OAuth tokens, and private-custody content are never published, and a
  contract test holds the publication equal to the manifest. The server holds
  no login to the primary database (the projection is a second database on
  the same cluster, and `pg_hba` admits the server's role to it alone), no
  network route to butler daemons, the dashboard API or the Switchboard, and
  the publisher has no credential to hold.
- **No Butlers model runs on the request path.** The server starts no session,
  imports no spawner, runtime, model SDK or Switchboard client, has no egress
  beyond the PostgreSQL port that serves its projection, and offers no
  sampling or elicitation.
- **Writes are proposals the owner gates.** `propose_fact` only inserts into a
  quarantined inbox. An inbox row is not an effect: it writes no domain store,
  goal or schedule. A deterministic domain job parks each valid proposal as an
  approval, and only the owner's per-event approval applies it, under the
  Approval Gates rules above. No standing rule approves one. An applied fact
  keeps `content_authority = third_party`, so it is never steering memory.
- **The owner holds kill switches** for each domain and for the whole
  surface, effective without a deploy.

Cloudflare and OAuth are not the only defense. Every tool call carries a
per-call assertion that the owner's Worker signs and the server verifies at
home, the hop bearer is checked against a stored digest, the server runs in its
own default-deny namespace, and responses are scanned for secrets and canaries
and audited. The Worker's signing key never enters Butlers: the server holds
only verification keys and a bearer digest, so the surface adds no
process-bound deployment control key.

**Limitations:**

- The gateway and the clients see every response the manifest allows.
- A client model can be steered by content it reads (prompt injection) into
  reading any enabled domain and leaking it through its own tools. Every
  layer past Cloudflare authenticates the owner's session, not the owner's
  intent, and Butlers cannot see the client's intent. Per-client budgets,
  volume alerts, audit and per-domain kill switches bound and detect this;
  they do not prevent it.
- Automatic approval of proposals is not admitted. It would turn outside
  content into a write without a per-event tap, so it needs its own owner
  decision.
- Several permissions on the database cluster are already
  superuser-equivalent, and each reaches primary-read-all with or without the
  publisher: exec, attach or debug access to the instance pods, creating pods
  beside them, reading the cluster's Secrets (which hold the replication
  role's client certificate), writing the cluster's configuration, and access
  to its node or disk. They stay with the owner. The publisher adds none of
  them.

## Credential Management

Runtime and user credentials follow a **three-tier authority model**. Except
for a process-bound deployment control key explicitly enumerated in the narrow
exception below, every credential has exactly one authoritative storage
location in one of these tiers:

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

### Narrow exception: process-bound deployment control keys

An asymmetric key used only to authenticate one trusted deployment process to
another MAY live outside the three credential tiers when all of the following
hold:

- it is control-plane material, not a provider, connector, or owner-account
  credential;
- the private key is provisioned through a dedicated file-backed deployment
  secret mounted only into the process that signs, never through an environment
  value, `CredentialStore`, `butler_secrets`, `entity_info`, or git-tracked
  configuration;
- recipients receive only the non-secret public verification key;
- its protocol, process boundary, provisioning, rotation, restart, and
  fail-closed behavior are specified before implementation; and
- generic Secrets APIs, model/runtime children, logs, telemetry, browser
  payloads, and environment diagnostics cannot expose, create, or shadow it.
- when the signing process launches untrusted runtime children, every concurrent
  invocation has its own kernel-enforced filesystem and process domain: one
  child cannot inspect or modify another invocation's staged credentials or
  process state, and the complete descendant domain is terminated and fenced
  before staged output is validated or persisted. A shared child identity,
  directory permissions, `no_new_privs`, a global lock, or process-group
  handling alone does not satisfy this boundary.

`RUNTIME_PROBE_CONTROL_SIGNING_KEY` is the first approved use of this exception.
The owner adopted this amendment and its refined deployment-key contract on
2026-08-10. It authenticates the private Dashboard API/scheduler control client
to Switchboard. The Dashboard API process is the only private-key reader;
Switchboard and the all-butlers runtime receive verification material only.
Missing or mismatched key material makes the control plane unavailable and
never falls back to a credential tier, unsigned request, or shared bearer
secret. Adding another process-bound deployment control key requires an
explicit doctrine and capability-spec amendment; this exception is not a
general file-secret escape hatch.

The owner approved `GOAL_BROKER_SIGNING_KEY` as the second use on 2026-10-14
(RFC 0038, decision 6). Only the Switchboard control-plane process reads it, to
sign call assertions; domain butlers receive verification keys only. Its
provisioning, rotation, and fail-closed behavior are specified in RFC 0038 D7.
These two are the only approved uses.

### Resolution Rules

- Env vars are NOT automatic overrides. For Tier 1, `CredentialStore.resolve()` only
  reads `os.environ` when `env_fallback=True` is explicitly passed (disabled by default).
  Tier 0 is the only tier where `os.environ` is the authoritative source.
- When a connector needs a credential, it MUST read from the authoritative tier.
- New runtime or user credentials MUST be classified into a tier when added.
  A process-bound deployment control key MUST instead satisfy and be named by
  the narrow exception above before implementation.

### Constraints

- Credentials must never appear in git-tracked configuration files.
- Credentials must never appear in session logs or tool call payloads sent to
  the dashboard.
- Inbound bearer material (one-time codes, password-reset and magic links, Telegram login codes)
  must never be persisted in ingest payloads; only a typed placeholder recording that an auth
  artifact arrived is kept (`openspec/specs/connector-base-spec`, "Bearer material never persists").
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
reverse-lookup (`resolve_contact_by_channel` in `src/butlers/identity.py`) is
required by RFC 0004 Amendment 4 to read only
`public.identity_channel_projection` joined to `public.entities`. Relationship
atomically maintains the derived projection from its eligible channel facts;
it remains the sole canonical writer. Public placement does not mean public
access: named runtime readers receive only read access, Relationship alone
receives runtime write access, and bootstrap replay must preserve the exact
ACL and forced row-policy contract. Missing, ambiguous or unavailable projected
identity cannot confer owner authority; inbound misses follow the existing
unknown-sender flow without a private-schema fallback. The implementation and
environment-specific backfill/cutover require separate evidence after adoption
of the amendment; this document does not assert that they have shipped.

Under the seam law (RFC 0004 Amendment 3), `relationship.entity_facts` remains
the single source of truth for all non-secret identifiers and relationships,
while `public.entity_info` holds secured credentials and its existing narrow
technical-configuration whitelist. Neither credentials nor private attribution
or evidence packets enter the channel projection. Telegram handles retain their
canonical `telegram:` prefix. This enables:

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
nothing more.** The network, firewall, and port-binding contract lives in
[RFC 0008](../legends-and-lore/rfcs/0008-deployment-network-security.md).
Containers receive only the environment they declare, and goal invocations run
in the session sandbox with only `PATH`, their model credential, their MCP config
and adapter-owned pointers to files staged inside the sandbox.

### Principles

1. **Bind to localhost unless there is a specific reason not to.** Tailscale
   serve handles external HTTPS exposure. The public read surface (RFC 0039)
   is reached only through the owner's MCP gateway.
2. **Egress by exception.** A service should not join the `egress` network
   unless it calls external APIs. This is a membership convention: the
   firewall covers only the `egress` bridge and no network is `internal: true`
   (see RFC 0008).
3. **Allowlist, not blocklist, for private network access.** The firewall blocks
   all private subnets then punches holes for specific tailnet hosts.
4. **No secret values in compose files.** Infrastructure bootstrap vars only.
   Runtime and user credentials are DB-first through their authoritative tier.
   Each process-bound deployment control key named above is file-mounted
   only into its signing process and never supplied as a Compose environment
   value or `CredentialStore` fallback.
5. **No `privileged: true`, no Docker socket mounts, no `cap_add`.** Containers
   run with default Docker capabilities.
6. **Default-deny ingress on Kubernetes.** Every Butlers namespace denies
   ingress by default and allows only named peers. The public read surface runs
   in its own namespace, reachable only from the gateway's proxy and able to
   reach only its projection database.

## Anti-Patterns

- Adding application-level encryption that duplicates filesystem/database
  encryption.
- Building multi-tenant access controls for a single-user system.
- Trusting LLM sessions to self-enforce security boundaries (use grants, the
  broker, domain assertion checks, and approval gates instead).
- Letting a task prompt, tool description, or message body widen a grant.
- Storing credentials in `butler.toml`, `CLAUDE.md`, or any git-tracked file.
- Assuming sender identity based solely on channel identifiers without
  confirmation for high-stakes actions.
- Logging full credential values in session logs or error messages.
- Binding Docker ports to `0.0.0.0` (exposes services to the entire LAN).
- Putting an OAuth layer or an internet route in front of a butler daemon's or
  the dashboard API's MCP endpoint. Only the public read surface is
  internet-reachable, and only through the gateway (RFC 0039).
- Adding services to the `egress` network when they don't call external APIs.
- Hardcoding tailnet IPs in compose files (use `ALLOWED_TAILNET_HOSTS` and
  dynamic resolution instead).
- Using `os.environ.get()` directly for API keys or tokens in connector or
  butler code (use `CredentialStore.resolve()` instead).
