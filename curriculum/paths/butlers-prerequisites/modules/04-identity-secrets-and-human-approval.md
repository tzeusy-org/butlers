# Identity, Secrets, And Human Approval

Estimated smart-human study time: 8 hours

## Why This Module Matters

Butlers acts on behalf of one owner. That makes identity, credentials, OAuth, side-effect approval, and redaction central safety concepts rather than peripheral features.

## Learning Goals

- Explain canonical owner/contact identity and entity anchoring.
- Understand DB-first credential resolution and OAuth refresh-token handling.
- Reason about API authorization, trusted callers, and route trust boundaries.
- Apply approval-gate and tool-sensitivity concepts to side-effecting tools.

## Subsection: Owner Identity, Contacts, And Trust Context

### Why This Matters Here

Routing, notifications, memory, OAuth, approvals, and unknown-sender handling depend on canonical identity records in shared tables.

### Technical Deep Dive

Identity systems separate an actor from their channel-specific handles. One person may have an email address, Telegram chat ID, Discord account, and OAuth refresh token. A contact registry maps these identifiers to a stable contact or entity.

Trust context answers: who sent this, through which channel, which owner or contact does it map to, and what authority should it carry? Dropping source metadata can break replies, misroute requests, corrupt provenance, or grant actions to the wrong actor.

### Where It Appears In The Repo

- `docs/concepts/identity-model.md`
- `docs/identity_and_secrets/owner-identity.md`
- `src/butlers/identity.py`
- `src/butlers/lifecycle.py`
- `alembic/versions/core/core_076_calendar_event_columns_and_entities.py`

### Sample Q&A

- Q: Why not store only raw sender IDs in routed messages?
  A: Raw IDs are channel-specific; the system needs stable contact/entity identity for trust, replies, memory, and audit.
- Q: What can break if `request_id` or sender context is dropped?
  A: Deduplication, traceability, reply routing, provenance, and authorization decisions.

### Progress

- [ ] Exposed: I can define contact, contact info, owner role, entity, channel identity, and request context.
- [ ] Working: I can explain why identity is shared through `public`.
- [ ] Contribution-ready: I can identify a route or tool path that must preserve sender context.

### Mastery Check

Target level: `contribution-ready`

You should be able to review an ingress, route, or notification change and verify it preserves identity and trust context.

## Subsection: Credentials, OAuth, And Secret Boundaries

### Why This Matters Here

Integrations use credentials and refresh tokens. The repo intentionally prefers DB-backed secret resolution over broad runtime environment exposure.

### Technical Deep Dive

Secrets should have a lifecycle: create, store, resolve, use, rotate, revoke, and redact. DB-first resolution allows the dashboard and runtime code to agree on credential state. Environment variables are useful for bootstrap or local overrides, but broad env exposure to spawned processes increases leak risk.

OAuth authorization-code flow redirects a user to a provider, receives a callback with a code, validates state to prevent CSRF, exchanges the code for tokens, and stores the refresh token securely. Error messages and list endpoints must avoid leaking secret values.

### Where It Appears In The Repo

- `docs/data_and_storage/credential-store.md`
- `docs/identity_and_secrets/oauth-flows.md`
- `docs/identity_and_secrets/cli-runtime-auth.md`
- `src/butlers/credential_store.py`
- `src/butlers/api/routers/oauth.py`
- `src/butlers/google_credentials.py`

### Sample Q&A

- Q: Why is OAuth `state` security-relevant?
  A: It binds the callback to the initiating request and helps prevent CSRF or account-linking confusion.
- Q: Why avoid passing all secrets to every runtime subprocess?
  A: Subprocesses should receive only the credentials required for their scoped work, reducing leak impact.

### Progress

- [ ] Exposed: I can define credential store, refresh token, access token, OAuth state, CSRF, and redaction.
- [ ] Working: I can explain DB-first credential resolution.
- [ ] Contribution-ready: I can identify where a secret might accidentally be logged or exposed.

### Mastery Check

Target level: `contribution-ready`

You should be able to inspect an integration flow and explain how credentials are stored, resolved, masked, and kept out of unsafe subprocess environments.

## Subsection: Approval Gates And Sensitive Tools

### Why This Matters Here

The system can send messages, update data, and perform high-impact actions. Some calls must be parked, approved, denied, or audited.

### Technical Deep Dive

An approval gate inserts policy between intent and side effect. Instead of allowing an LLM to directly perform every action, the system classifies tool calls by sensitivity, redacts sensitive arguments, records decisions, and allows human review where needed.

Tool sensitivity can be declared explicitly through metadata or inferred through heuristics. Approval records should be append-only enough for audit, and denied or pending calls must not perform the side effect.

### Where It Appears In The Repo

- `docs/modules/approvals.md`
- `src/butlers/modules/approvals/`
- `src/butlers/modules/base.py`
- `tests/api/test_api_approvals.py`
- `tests/modules/test_module_approvals.py`

### Sample Q&A

- Q: Why is redaction part of approval design?
  A: Review/audit surfaces may need context without exposing full secret or sensitive argument values.
- Q: What is unsafe about bypassing approval in a helper function?
  A: It can perform a side effect without policy evaluation or audit history.

### Progress

- [ ] Exposed: I can define approval gate, sensitive argument, redaction, pending, approved, and denied.
- [ ] Working: I can explain how a sensitive tool call should be intercepted.
- [ ] Contribution-ready: I can identify whether a new side-effecting tool needs sensitivity metadata.

### Mastery Check

Target level: `contribution-ready`

You should be able to review a side-effecting tool and state its approval, redaction, and audit requirements.

## Module Mastery Gate

- [ ] I can explain the identity model used for routing and memory provenance.
- [ ] I can describe OAuth and credential storage risks.
- [ ] I can identify approval-gate requirements for outbound or high-impact actions.
- [ ] I can choose tests for auth, OAuth, credentials, or approval changes.
