## Why

Relationship's current email-enrichment heuristic can observe inbound recurrence,
but it cannot prove that Messenger sent anything to the same person.  The live
email module treats SMTP acceptance as `sent`; its Gmail Sent-ID cache is
in-memory priority policy only, while current audit, notification, and inbox
records contain content and therefore cannot be reused as correspondence
evidence.

## What Changes

- Add a Messenger-private, metadata-only email correspondence ledger with the
  four truthful states `accepted`, `confirmed`, `failed`, and `unknown`.
- Require an explicit provider-Sent confirmation before an outbound record can
  contribute to bidirectional correspondence.  Generic SMTP acceptance remains
  non-confirming and eventually becomes `unknown`.
- Define authenticated-ingress account epochs with transient provider-age
  validation and atomic opaque deduplication, provider-native exact-reference
  send/reconciliation through fenced dispatch/send-report and confirmation-report
  callbacks, exact same-account and peer inbound correlation, explicit temporal
  peer-alias authority, owner-approved complete account-universe continuity, a
  180-day evidence window, full-window coverage semantics, and bounded
  freshness-aware aggregates for Relationship.  Negative evidence is literal-peer
  only and unavailable for an entity with an active alias or incomplete universe.
- Add a narrow, migration-managed `SECURITY DEFINER` aggregate interface so
  Relationship can consume per-entity evidence without selecting or enumerating
  Messenger ledger rows.
- Bind that RFC 0010 exception to one daily, 100-entity maximum,
  zero-LLM `email_correspondence_enrichment` `dispatch_mode="job"` consumer;
  it has no MCP/API/on-demand/interactive aggregate path and replaces an
  otherwise bounded 101-session-per-batch MCP fan-out.
- Require a scheduler-level protected-job registry for that exact
  `email_correspondence_enrichment` identity.  It rejects generic
  `schedule_trigger` before dispatch and `schedule_create`/`schedule_update`
  before persistence, so an interactive caller cannot create an alias, alter the
  cron, or repurpose another schedule while the fixed configuration-owned TOML
  system schedule remains runnable.
- Make correspondence-path audit, notification, inbox, metrics, and error
  handling metadata-only from trusted pre-route candidate through admission and
  outcome; content-bearing existing stores are never evidence or a new mirror
  for this capability.
- Record RFC 0023, an implementation plan, migration/rollback sequencing, and
  owner gates.  This change is planning only: it does not execute migrations,
  contact a provider, scan a mailbox, backfill data, deploy, or mutate live data.

## Capabilities

### New Capabilities

- `email-correspondence-ledger`: Private outbound evidence, state transitions,
  provider-Sent confirmation, correlation, retention, and truthful freshness.

### Modified Capabilities

- `module-email`: Email sends produce a privacy-safe, typed correspondence
  outcome instead of claiming that SMTP acceptance is bidirectional proof.
- `butler-messenger`: Messenger owns the private ledger and deterministic
  maintenance while retaining direct approved channel egress.
- `connector-gmail`: Gmail may reconcile only provider references issued by a
  Messenger-native send attempt; it must not enumerate or backfill Sent mail for
  this feature.
- `connector-base-spec`: Connector correspondence calls authenticate to
  Switchboard with an immutable connector/account principal before leasing or
  reporting provider evidence.
- `butler-switchboard`: Switchboard brokers authenticated connector leases and
  Messenger-native sends and categorical confirmation reports to Messenger,
  creates authenticated ingress epochs, and avoids generic raw-error routing
  persistence.
- `butler-relationship`: Relationship consumes only a bounded, freshness-aware
  aggregate when evaluating email identity evidence.
- `database-security`: Role grants and a narrowly scoped aggregate function
  protect the private ledger from cross-schema reads.
- `core-notify`: Email correspondence delivery cannot write content-bearing
  generic notification or outbound-inbox mirrors.

## Impact

Future implementation touches Messenger's migration chain and configuration,
the shared email module and routing path, Switchboard notification persistence,
authenticated ingress and connector broker, the Gmail connector/client transport,
Relationship's separate deterministic known-entity enrichment job, PostgreSQL ACLs,
contract/migrated-database/provider tests, relevant OpenSpec specs, and the
Messenger/Relationship/Gmail documentation.  No source implementation,
migration execution, provider operation, or data operation is authorized by
this proposal.
