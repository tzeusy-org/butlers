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
  infrastructure. The system does not attempt to redact or encrypt prompt content.
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

Secrets (API keys, OAuth tokens, database passwords) are managed through:

- **Environment variables** for deployment-level secrets (database URL, LLM API
  keys).
- **Credential store** in the database for butler-specific secrets (OAuth
  refresh tokens, service-specific API keys).
- **Dashboard OAuth flow** for configuring per-service credentials interactively.

**Constraints:**

- Credentials must never appear in git-tracked configuration files.
- Credentials must never appear in session logs or tool call payloads sent to
  the dashboard.
- The credential store uses the database's access controls, not application-level
  encryption. This is consistent with the trust model: if the database is
  compromised, the attacker already has access to the data the credentials
  protect.

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
