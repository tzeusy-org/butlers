## Context

Messenger is the only butler with email send tools. `EmailModule` sends via SMTP,
returns `sent`, and writes a `gmail_send` audit row with recipient, subject, and
raw error text. Routed `notify.v1` email can also land in
`switchboard.notifications` and `switchboard.message_inbox`. None of these is
reliable or privacy-safe evidence that mail reached the provider's Sent folder.

Relationship's `run_email_identity_enrichment` infers recurrence from inbound
`public.ingestion_events` because no trustworthy outbound signal exists. The
identity source of truth is active literal `relationship.entity_facts` rows with
`predicate='has-email'`.

This is a P3 enrichment signal, so v1 builds only the outbound half: a private
ledger and provider-Sent confirmation. The inbound half reuses Relationship's
existing signal.

## Goals / Non-Goals

**Goals:** record outbound email as private metadata; prove `confirmed` only from
the provider's Sent state; give Relationship a bounded, read-only aggregate;
keep indeterminate outcomes `unknown`.

**Non-Goals:** mailbox scans, Sent enumeration, or backfill; content, headers,
hashes, or raw provider responses in the ledger; reusing content-bearing stores
as evidence; negative (`false`) correspondence claims; alias inference.

## Decisions

### 1. One private, allowlisted ledger

`messenger.email_correspondence` holds only: opaque idempotency key, provider,
account reference, normalized bare peer address, optional provider message ID,
state, and timestamps (`intent_at`, `dispatch_started_at`, `confirmed_at`,
`confirm_deadline`). No JSONB, free text, subject, body, headers, or error text.
It does not revive the generic delivery tables retired by migration 003.

States: `unknown` (before dispatch, or after a crash/timeout/expired deadline),
`accepted` (transport accepted), `confirmed` (provider proved the exact message
is in Sent), `failed` (rejected before acceptance). Transitions are monotonic
except `accepted -> unknown` at the deadline. Only `confirmed` counts as evidence.

### 2. Commit intent before egress; no blind retry

After approval and before calling the provider, Messenger inserts or locks the
row for a server-derived idempotency key. If that commit fails, nothing is sent.
A crash after dispatch leaves the row `unknown`; recovery may confirm a known
message ID but never resends unless the provider's idempotency contract is bound
to the same key. SMTP has no such contract and is never retried blindly.

### 3. Messenger confirms Sent state itself

A disabled-by-default Gmail API send path uses Messenger's own OAuth credential.
`users.messages.send` returns the message ID; the maintenance job later calls
`users.messages.get` for that exact ID (metadata only) and marks the row
`confirmed` when the `SENT` label is present. No list, search, history, or cache
is used, and the Gmail connector's Sent-ID cache is not evidence. SMTP sends stay
`accepted` and expire to `unknown`.

Keeping confirmation inside Messenger removes the need for a Switchboard broker,
connector principals, and fenced lease/report callbacks.

### 4. Read-only aggregate view instead of `SECURITY DEFINER`

`messenger.v_confirmed_email_outbound` returns, per normalized peer, a capped
confirmed count and the last confirmed time within the last 180 days. Relationship
receives schema `USAGE` and `SELECT` on this view only; it cannot read the table.

This is the RFC 0010 pattern (read-only view, deterministic daily batch,
migration-audited, cost-justified: an MCP fan-out would spend an LLM session per
lookup for zero-reasoning SQL). It replaces the earlier `SECURITY DEFINER`
entity-ID function because a plain view needs no definer ownership, search-path
hardening, or cross-chain activation routine: it lives entirely in Messenger's
migration chain. The view exposes addresses Relationship already holds as
`has-email` facts, plus only counts and timestamps.

### 5. Relationship combines legs; result is `true` or `null`

A deterministic `email_correspondence_enrichment` job looks up at most 100 active
literal `has-email` addresses. `bidirectional=true` requires a confirmed outbound
row and Relationship's existing inbound recurrence for the same address within
180 days. Anything else is `null`; v1 never returns `false`, since proving absence
needs full account coverage (deferred).

No protected scheduler registry is added: the view is read-only and aggregate, so
a manual trigger merely reruns the same deterministic job, as with RFC 0010's
consumer.

### 6. Retention and rollback

Rows are hard-deleted 180 days after `intent_at` by the maintenance job. Rollback
disables the send flag and revokes the view grant; Relationship then returns
`null`. Downgrade drops the view first and refuses to drop a non-empty table.

## Risks / Trade-offs

- **SMTP users get no positive proof** -> reported as `accepted` then `unknown`.
- **Inbound leg is heuristic and not account-bound** -> acceptable for a P3
  signal that only adds `true`; stronger inbound proof is deferred.
- **Relationship can see which addresses Messenger mailed** -> limited to
  counts/timestamps over addresses it already stores.
- **Crash duplicates a message** -> no blind retry; confirm by exact ID.

## Deferred

Out of v1; each needs its own change if pursued:

- Authenticated connector ingress epochs, coverage epochs, and qualified-inbound
  observations (previous `butler-switchboard`, `connector-base-spec` deltas).
- Switchboard native-send/confirmation broker with fenced leases (previous
  `connector-gmail` delta); only needed if confirmation moves to the connector.
- Negative evidence (`false`) and the complete account-universe continuity model.
- Versioned provider peer-alias authority.
- Scheduler protected-job registry (previous `core-scheduler` delta).
- Suppressing content in `gmail_send` audit, `switchboard.notifications`, and
  `switchboard.message_inbox` (previous `core-notify` delta); a separate privacy
  migration.
- `SECURITY DEFINER` entity-ID aggregate and post-chain activation routine.
