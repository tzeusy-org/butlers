# RFC 0024: Messenger-Private Email Correspondence Ledger

**Status:** Proposed - Owner Option A approved planning only

**Date:** 2026-08-13

## Summary

This RFC proposes a privacy-minimized way for the Relationship butler to learn
that a person has had genuine two-way email correspondence with the owner.  It
does not treat email delivery as proof.  Messenger records a private outbound
metadata intent, and only an enabled provider that proves the exact message is
in its Sent representation may confirm that intent.  Relationship receives one
bounded aggregate per requested entity; it cannot select or enumerate raw
Messenger rows.

The protocol is intentionally conservative.  SMTP acceptance is `accepted`,
not `confirmed`; providers that cannot provide exact Sent proof are `unknown`.
An outbound confirmation must correlate with qualifying inbound evidence for
the same canonical account and peer within 180 days before the aggregate can
say the correspondence is bidirectional.

This is a design/authorization record, not implementation authorization.  It
does not call a provider, scan a mailbox, backfill mail, migrate a database,
deploy code, or touch live data.

## Motivation

The current Relationship email-enrichment workflow is deliberately
inbound-oriented.  It looks for recurring inbound email observations because
there is no trustworthy cross-butler outbound record.  The live shared
`EmailModule` can only report that SMTP accepted a message; it does not know a
provider message ID or Sent status.  The Gmail connector's bounded in-memory
Sent-ID cache is a priority heuristic, not durable evidence, and it is filled
by a broad Sent-mail traversal.

Several existing stores are unsuitable alternatives:

- `public.audit_log` receives current `gmail_send` audit payloads containing
  recipient/subject/raw error text.
- `switchboard.notifications` and `switchboard.message_inbox` can contain the
  routed message, recipient, and full notification context.
- The retired Messenger delivery tables were unwired from real egress and were
  intentionally removed with an empty-only data-retention safeguard.

None proves provider-Sent delivery, and each is content-bearing.  Reusing them
would create a false evidence trail and widen privacy exposure.

## Scope

### In scope when implementation is separately approved

- A private Messenger ledger for allowlisted outbound correspondence metadata.
- Transactional pre-send intent, opaque idempotency, conservative recovery, and
  exactly bounded provider reconciliation.
- Provider-Sent confirmation for a provider/account whose explicit capability
  contract proves an exact known message is in Sent.
- Authenticated-ingress epoch/account binding, same-account and exact-peer
  inbound correlation, and full-window coverage over a 180-day window.
- An RFC 0006 exception limited to one `SECURITY DEFINER` aggregate function
  available to Relationship.
- Hard retention, truthful freshness, migration safety, provider/ACL/privacy
  tests, a capability-gated rollout, and a fail-closed rollback.

### Out of scope

- Any mailbox scan, Sent listing/search/history traversal, historical backfill,
  broad reconciliation sweep, or use of the current Gmail Sent-ID cache as
  proof.
- Provider mutation or a provider call under this planning work.
- Persisting a subject, body, header, attachment, raw provider object, audit
  text, raw error, credential, content hash, display name, or full request
  envelope in a correspondence-specific store or aggregate.
- A generic delivery queue, dead-letter/retry subsystem, health endpoint, or
  relationship fact that materializes correspondence content.
- Inferring peer/account aliases from display names, domains, plus-addressing,
  thread context, or stale facts.
- A claim that pre-existing generic content persistence has been retroactively
  removed.  Those surfaces are not evidence; a wider historic privacy cleanup
  requires a separately approved change.

## Normative model

### Private data minimization

`messenger.email_correspondence` is a private, migration-owned table.  Its
allowlisted data is:

| Category | Fields |
|---|---|
| Opaque identity | Row ID and server-derived opaque idempotency key |
| Provider routing | Provider category and canonical provider account reference |
| Peer | One normalized bare RFC-5322 peer address |
| Provider proof | Optional provider message reference and optional provider thread reference |
| State | `unknown`, `accepted`, `confirmed`, or `failed` |
| Time | Intent, dispatch start, acceptance, confirmation, failure, unknown, deadline, lease, and expiry timestamps |

The same private schema may contain only the following supporting tables, each
with a separate explicit column allowlist:

| Supporting record | Allowed metadata only |
|---|---|
| Qualified inbound observation | Server-generated opaque receipt ID, non-reversible account-scoped source-deduplication token, provider, canonical account reference, normalized bare peer address, authenticated ingress epoch ID, server `received_at`, and expiry |
| Coverage state | Provider, canonical account reference, coverage kind, opaque epoch ID, rolling `covered_from`/`covered_through`, last committed checkpoint time, categorical continuity state, and expiry/closure timestamps |
| Account-universe membership | Provider, canonical account reference, opaque complete-universe epoch ID, rolling `covered_from`/`covered_through`, last committed complete-configuration checkpoint, categorical continuity state, issued/expires/revoked/purge timestamps, and no peer/content fields |
| Native-send dispatch/report fence | Opaque dispatch ID, fence, deterministic send-report ID, principal and intent binding, categorical outcome, exact reference only after a valid report, and short dispatch/retention timestamps |
| Confirmation lease/report fence | Opaque lease ID, fence, deterministic report ID, principal binding, exact known reference, categorical outcome when recorded, and short lease/retention timestamps |
| Explicit alias authority | Opaque authority ID/version, provider, canonical account reference, entity ID, normalized bare peer address, issued/expires/revoked/purge timestamps, and a fixed approved-source category |

None contains JSONB/free-text metadata escape hatches.  An alias authority is
not a general address book: it is created only by an explicitly approved,
versioned writer, binds exactly one provider/account/entity/peer tuple, is
active only from issuance until its fixed expiry unless revoked sooner, and can
never be extended by correspondence activity.  Its maximum lifetime and purge
deadline are each 180 days from issuance; a revocation does not reset either
deadline.  A positive alias match requires the authority to be active at query
time and to have covered every qualifying evidence timestamp under the half-open
interval `[issued_at, expires_at)`: `issued_at <= evidence_at < expires_at`, and
no `revoked_at` at or before `evidence_at`.  A newly issued authority cannot
retroactively match an older observation.  An expired or revoked authority cannot
qualify new or retained evidence.

An account-universe epoch is a separate private, owner-approved complete inventory
of every canonical provider/account through which the owner can possibly send or
receive email correspondence, including disabled, unsupported, and unproven
accounts.  It is supplied only by that approved configuration contract: it is
never inferred from a mailbox, provider listing, historical envelope, or a
backfill.  Its membership records have no peer, content, credential, or raw
provider data and are never exposed through the Relationship aggregate.  A
membership is valid for at most 180 days from issuance and is purged by its own
deadline.  A still-complete, unchanged universe may roll to a newly approved
bounded successor which carries only its current membership, rolling
`covered_from`/`covered_through` interval, last complete-configuration checkpoint,
and categorical continuity state; it retains no historic membership list.  A
successor may extend `covered_from` only when the preceding complete epoch ended
without a gap and had the identical member set.  Account addition, removal,
rebinding, incomplete inventory, expiry, or a continuity failure closes
completeness and begins any later interval anew.  The aggregate may emit a
negative result only when a current complete-universe continuity interval spans
the full requested window; it must otherwise return `unknown`/`null`.

All new correspondence-specific audit events, logs, metrics, traces, leases,
caches, and aggregate outputs follow the same prohibition.  Existing public
audit/notification/inbox/ingestion stores are excluded as evidence and must not
be copied into the ledger.  The future correspondence route must also avoid
creating a new content-bearing generic notification or outbound-inbox mirror,
including when it fails before Messenger can admit an intent.

### State machine

The ledger has exactly four states:

| State | Meaning | Evidence status |
|---|---|---|
| `unknown` | Intent has not been dispatched yet, or a crash/timeout/unsupported confirmation left the outcome indeterminate | Never positive |
| `accepted` | The SMTP or provider transport accepted the message | Never positive |
| `confirmed` | The enabled provider proved the exact known message is in Sent for the same account | Eligible outbound leg only |
| `failed` | The provider/transport rejected before acceptance was known | Never positive |

`unknown` is purposeful: it avoids inventing a fifth persistence state and
avoids characterizing ambiguity as a negative outcome.  The presence of
`dispatch_started_at` distinguishes a newly admitted intent from an uncertain
post-dispatch attempt without recording a reason string.

Allowed transitions are:

```text
new transactional intent                  -> unknown
known pre-acceptance rejection             -> failed
known SMTP or fenced native-provider acceptance -> accepted
provider exact-reference Sent confirmation -> confirmed
timeout, crash, absent proof, deadline     -> unknown
```

`accepted -> unknown` is permitted only when the confirmation deadline expires;
no path converts `unknown` to a new external send by restart alone.  `confirmed`
is terminal.  No state implies recipient receipt, read, reply, or a relationship
fact.

### Transactional admission and idempotency

The normal email route remains Messenger-owned and approval-gated.  After the
route/approval system has constructed a valid native email command but before
the provider client is called, Messenger:

1. derives a server-only opaque idempotency key from trusted command/approval
   lineage (never from content);
2. inserts or locks the unique private intent in a database transaction;
3. aborts delivery if that transaction cannot commit; and
4. starts the non-transactional external send only after admission succeeds.

The provider call cannot be atomic with PostgreSQL.  A crash after dispatch has
started produces `unknown`; the recovery path may reconcile an already-known
exact provider reference but may not blindly re-send.  A retry is legal only
when an enabled provider documents idempotency for the same opaque key.  Generic
SMTP offers no such guarantee in this RFC.

An exact-reference capability has an additional disabled-by-default prerequisite:
Messenger must initiate one dedicated provider-native send through an
authenticated Switchboard internal broker after admission.  The Gmail connector
retains its provider credential but cannot initiate generic egress.  It accepts
only a one-time Messenger-issued native-send dispatch, carries subject/body and
recipient transiently in an explicitly non-persisting, non-generic route, calls
the provider's documented native send operation, and returns the stable exact
provider message/thread reference directly from that send response.  It reports
that direct result only through a separately authenticated, one-time
`correspondence.send.report` callback bound to the dispatch ID, fence, and
deterministic send-report ID.  Messenger atomically validates and consumes that
dispatch report before recording `accepted` with the exact reference, `failed`
for a known pre-acceptance rejection, or `unknown` for an indeterminate result.
An identical callback returns the previously recorded categorical result; an
expired, conflicting, cross-account, or altered-fence callback fails closed.
Only the recorded `accepted` exact reference can later receive a confirmation
lease.  The route cannot put transient content or the native result in a generic
notification, inbox, audit, log, trace, error, or retry record.  The connector
cannot manufacture a dispatch or use this path for any non-Messenger request.
A reference inferred from SMTP, an RFC `Message-ID`, the Sent cache, a
list/search result, or a later broad scan is never eligible for a lease.  Until
this native-send prerequisite is approved and implemented, all SMTP attempts
remain `accepted -> unknown` and are never leased for confirmation.

### Provider-Sent confirmation and reconciliation

Provider confirmation is a capability, not a generic assumption.  To enable a
provider/account, its owner must document a provider-native send operation that
returns an exact message reference directly and an operation that proves that
same reference is in Sent through an exact-ID metadata lookup.  This RFC does
not treat a native send response itself as Sent confirmation.  The result sent
to Messenger is restricted to:

- canonical account reference;
- exact provider message/thread reference;
- categorical native-send or confirmation outcome; and
- observed timestamps.

The Gmail connector owns Gmail credentials and future provider interaction, but
it does not contact Messenger directly.  It authenticates only to the
Switchboard broker tools `correspondence.send.dispatch`,
`correspondence.send.report`, `correspondence.confirmation.poll`, and
`correspondence.confirmation.report` with a secret-authority-managed scoped
transport credential.  Gmail polls `send.dispatch`; Switchboard returns at most
one Messenger-issued one-time native-send dispatch for that verified principal.
Its body/subject/recipient is transient in the dedicated response only and cannot
enter generic persistence.  Gmail then reports the direct native result through
`send.report`, bound to that dispatch ID/fence/deterministic report ID.  The
report carries only the direct exact reference when one was returned, a
categorical acceptance/rejection/indeterminate outcome, and allowed timestamps.
Switchboard derives the immutable connector and canonical account principal
server-side; it does not trust a connector-supplied provider, account, or
endpoint field.  This is a required implementation change: the current
`CachedMCPClient` accepts only an endpoint URL and client name, so its transport
(or its upstream framework boundary) must gain safe scoped-credential attachment
without logging or returning a credential.

Messenger atomically validates the broker-derived principal and dispatch binding
before it records the native result.  It never treats that result as Sent proof or
persists a provider response.  If the connector crashes after native send but
before the callback, it may retry the same `send.report` only; it cannot resend
the provider operation unless the provider's documented idempotency contract
permits the same opaque key.  An unreported or expired dispatch remains
`unknown`.

Only after Messenger has recorded an accepted direct exact reference, Switchboard
may request at most one opaque, short-lived, one-time Messenger confirmation
lease bound to that known exact account/reference.  The lease
contains only its opaque ID, fence, deterministic report ID, exact known
reference, and allowed timestamps.  The connector reports only that lease
material, a categorical outcome, and an observed timestamp.  Switchboard
forwards it via a dedicated scrubbed internal route; it must not consume a lease
separately.  Messenger is the sole atomic lease-consume-plus-outcome writer,
verifying broker context, principal, lease ID/fence/report ID, expiry, and
account/reference binding in the same transaction as its state change.  An
identical retry returns the recorded categorical result; a conflicting, stale,
expired, or altered-fence report fails closed.  This prevents a broker crash
from losing a valid report between routing and the Messenger commit.

The broker must bypass the generic Switchboard route error path, which currently
persists raw exception text in `switchboard.routing_log`; its route errors,
logs, metrics, and responses must be categorized and scrubbed before persistence.
For confirmation Gmail must never enumerate Sent mail, call `list`, search,
traverse history, read body/header content, backfill, mutate a provider resource,
or use `_sent_ids_cache` as confirmation evidence.  The only provider mutation
contemplated by the broader capability is the separately approved one-time native
send that returns the exact reference directly.

SMTP's successful `sendmail` call can produce `accepted` only.  If no enabled
provider capability confirms the exact message before its deadline, it becomes
`unknown`.  It is never true bidirectional correspondence.

### Authenticated ingress, same-account, and peer correlation

Existing `public.ingestion_events` cannot qualify merely because their
caller-populated envelope says Gmail or names an endpoint.  Before a provider
account is enabled, Switchboard must authenticate the connector transport,
derive its connector/provider/endpoint principal, verify it against an approved
canonical account binding, and assign an opaque authenticated-ingress epoch.
Only events accepted under that enforcement may emit one private, allowlisted
qualified-inbound observation.  The projection contains only provider category,
canonical account reference, normalized sender identity, epoch ID, and the
server-generated `received_at` time, captured once at authenticated ingress
admission.  It neither returns nor copies payload text, headers, event IDs,
thread IDs, raw data, or the raw envelope's source-observed timestamp.  Events
before the authenticated epoch, from a reset/gap/re-auth period, or with a
principal/account mismatch are excluded from proof.

The 180-day inbound timestamp is server `received_at`, not provider-observed
time.  To make the provider-to-receipt-delay and clock-skew gate enforceable,
the credential-bound provider adapter may derive one strictly typed, transient
provider-age assertion from its documented provider event-time field at the
authenticated ingress seam.  It is not a generic caller field or raw-envelope
field; Switchboard validates it only against the derived principal, server clock,
and owner-approved maximum age/future-skew budget, then discards it.  The
assertion must never reach `public.ingestion_events`, the private observation,
coverage state, logs, traces, metrics, errors, or the aggregate, and it is never
the 180-day evidence time.  A missing, malformed, future-skewed, or over-age
assertion emits no qualified observation and closes or blocks the inbound epoch,
so freshness becomes indeterminate rather than treating a delayed or replayed
old event as new receipt-time evidence.

At the accepted-ingress seam, Switchboard derives from the authenticated
provider's immutable source-event key a non-reversible, account-scoped opaque
deduplication token using a secret held outside the database.  It must make the
first qualified-observation insert and any corresponding coverage advance in the
same accepted-ingress transaction through a Messenger-owned, non-generic
projection writer; a best-effort post-acceptance call is forbidden.  The private
unique token is the only retained deduplication material: no raw source event ID
or provider payload is stored.  A replay or retry obtains the existing
categorical result and cannot create another observation, replace the original
`received_at`, or refresh coverage.

The peer is eligible only when either:

1. an active literal `relationship.entity_facts` row with
   `predicate='has-email'` exactly matches the shared normalized bare address;
   or
2. an explicit, provider-specific peer-alias authority with the lifecycle above
   binds that exact provider/canonical-account/peer to an existing Relationship
   entity, is active at query time, and covers every qualifying evidence time.

No display-name, same-domain, plus-address, local-part, thread, or
retracted/superseded fact inference is allowed.  Same account means the
transport-authenticated canonical provider account equals the ledger account
exactly.  Provider account aliases are out of scope; string similarity of
addresses is never an account mapping.

The window is exactly 180 days.  A confirmed outbound and matching qualified
inbound observation must both fall within the rolling window.

### Relationship aggregate and schema boundary

RFC 0006 forbids raw cross-schema reads.  This RFC invokes RFC 0010's narrow
cross-butler read exception only for the fixed aggregate consumer described
below, not as a reusable data plane:

```sql
public.relationship_email_correspondence_summary(entity_ids uuid[])
```

The function accepts a validated, bounded (maximum 100) non-duplicate vector of
entity IDs and returns no more than one fixed-shape row per requested ID:

| Returned field | Constraint |
|---|---|
| `entity_id` | Echo of a supplied Relationship entity ID |
| `confirmed_outbound_count` | Capped aggregate (0 through 3), not a raw count |
| `last_confirmed_outbound_at` | Timestamp only |
| `last_qualifying_inbound_at` | Timestamp only |
| `bidirectional` | `true`, `false`, or `null` |
| `freshness` | `fresh`, `stale`, or `unknown` |

It returns no address, account, provider, message/thread ID, raw ledger row,
raw inbound row, content, audit text, or any enumeration capability.  The
migration-admin activation routine creates it with `SECURITY DEFINER`,
`SET search_path = pg_catalog`, explicitly schema-qualified table names,
`REVOKE ALL ... FROM PUBLIC`, and `GRANT EXECUTE` only to
`butler_relationship_rw`.  Relationship gets no direct table select,
connector/dashboard/public roles get no execute, and the function does not
accept arbitrary addresses or SQL-like predicates.

#### RFC 0010 scheduled-reader guardrails

This exception is permitted only while all RFC 0010 reuse criteria remain true:

1. **Database-enforced narrow reader.** The fixed vector signature, 100-ID
   ceiling, fixed aggregate output, `SECURITY DEFINER` owner, hardened search
   path, explicit schema qualification, `PUBLIC` revocation, exact
   `butler_relationship_rw` execute grant, and absence of any raw-table grant
   are a database-enforced narrow reader.  They are not application convention
   or a grant to enumerate Messenger data.
2. **Fixed protected scheduled batch.** The sole aggregate consumer SHALL be
   the deterministic `email_correspondence_enrichment` job.  Future
   implementation SHALL register its Relationship wrapper in
   `src/butlers/scheduled_jobs.py` and declare exactly one daily `35 6 * * *`
   schedule in `roster/relationship/butler.toml` with
   `dispatch_mode="job"` and `job_name="email_correspondence_enrichment"`.
   The canonical protected identity is the normalized tuple of Relationship,
   `source='toml'`, `name="email-correspondence-enrichment"`, that exact cron,
   job dispatch mode, job name, and its configuration-declared job arguments.
   The job processes at most 100 already-resolved entity IDs per run, receives no
   prompt, and has zero-LLM behavior.  Future implementation SHALL define a
   scheduler-level protected-job registry or allowlist for that identity;
   deterministic handler registration alone is not authorization to run it.
3. **No public or interactive consumer.** The exception authorizes no
   MCP/API/on-demand/interactive aggregate path: no Relationship or Messenger
   MCP tool, dashboard/API route, Switchboard route, LLM-session tool call,
   direct human query, or scheduled prompt may invoke it.  The scheduler-level
   protected-job registry SHALL reject `schedule_trigger` attempts for the
   protected identity before dispatch.  It SHALL reject `schedule_create`
   attempts to persist a runtime or alias schedule for the protected job, and
   `schedule_update` attempts to mutate the canonical identity or change another
   schedule into it, before persistence.  A generic caller cannot substitute a
   name, cron, dispatch mode, or job arguments to reach the handler.  The normal
   configuration synchronization and scheduler tick retain the one fixed TOML
   schedule as the only permitted system invocation.
4. **Migration/config ownership and regression evidence.** The reader and its
   grants remain migration-managed and reversible.  The protected identity is
   owned by the roster configuration rather than interactive runtime CRUD; any
   durable enforcement metadata or database constraint introduced to support it
   SHALL also be migration-managed, reversible, and auditable.  Migrated
   PostgreSQL tests SHALL prove the fixed signature, ownership, ACLs, bounded
   output, direct select denial, and failed-closed partial topology.  Scheduler
   tests and the static packet contract SHALL prove protected `schedule_trigger`,
   `schedule_create`, and `schedule_update` rejection before dispatch or
   persistence, while the exact TOML configuration sync and due system tick still
   run the handler.  Each rejected generic path SHALL return an auditable
   rejection category, emit a bounded metric and security audit event, and expose
   no entity ID, account, peer, job arguments, or other correspondence metadata.
5. **Concrete bounded cost case.** At the 100-entity ceiling, the compliant
   alternative is a Relationship LLM session plus up to one Messenger LLM
   response session per entity, or up to 101 LLM sessions per daily batch.  The
   registered deterministic job performs the same bounded aggregate with zero
   LLM sessions.  A new batched MCP tool would still create an interactive
   surface and is outside this exception; it cannot be used to weaken the cost
   or scheduling boundary.

The authenticated Switchboard broker is a separate, narrow control-plane
exception.  It has no direct Messenger table grant and no generic cross-schema
read.  Its four connector tools are limited to delivering one principal-bound
Messenger-issued native-send dispatch, atomically recording one fenced native
send result, issuing one principal-bound confirmation lease, or atomically
recording one fenced categorical confirmation report through Messenger; no tool
can list ledger rows or call the Relationship aggregate.

Qualified inbound projection is a separate, equally narrow write exception:
Messenger owns one fixed projection `SECURITY DEFINER` function
`messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
bytea)`, executable only by `butler_switchboard_rw` under the authenticated
internal ingress route.  Its six inputs are, in order, only Switchboard-derived
provider, canonical account reference, normalized peer, authenticated ingress
epoch ID, once-captured server receipt time, and opaque account-scoped
deduplication token.  It returns only the fixed categorical value `recorded` or
`duplicate`; no principal, source-event ID, provider event time, content, or
other caller-controlled context is an argument.  The migration-admin installer
assigns an explicit designated definer owner, `SET search_path = pg_catalog`, and
explicitly schema-qualified object references; it revokes all function execution
from `PUBLIC`, grants `USAGE ON SCHEMA messenger` and `EXECUTE ON FUNCTION
messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
bytea)` only to `butler_switchboard_rw`, and grants no other Messenger object
access to that role.  Connector, dashboard, Relationship, and other butler roles
get neither schema usage, execution, nor direct table access.  It exposes no
generic writer or table access.  Switchboard invokes it in the same transaction
as the correspondence-qualified accepted-ingress decision.  The private unique
token returns the first categorical projection result on a retry without changing
receipt time or coverage.  A function failure leaves the event unqualified and
coverage unavailable rather than triggering a best-effort later projection.
Migrated PostgreSQL tests must assert its owner/search path/schema qualification,
fixed signature, minimal schema-usage/exact-execute grants and denials,
caller-controlled search-path resistance, atomic deduplication, and absence of a
generic cross-schema write surface.

Relationship invokes only
`public.relationship_email_correspondence_summary(uuid[])` from the RFC 0010
scheduled `email_correspondence_enrichment` job above.  It may use fresh `true`
as a structured signal under its existing provenance/approval rules, but it must
not materialize raw correspondence metadata in a fact or log.  The existing
`run_email_identity_enrichment` unresolved-sender discovery/proposal job remains
separate and inbound-only; this RFC neither replaces it nor relabels its
recurrence heuristic as provider-confirmed correspondence.  Neither job may call
a provider or fall back to inbound-only recurrence as a positive correspondence
proof.

### Freshness and coverage semantics

Each enabled canonical account maintains metadata-only coverage epochs for its
authenticated inbound ingestion and provider-native outbound ledger/confirmation
path.  An inbound epoch records a contiguous `covered_from`/`covered_through`
interval only after a successful committed authenticated checkpoint.  An outbound
epoch begins only after an approved capability enforcement checkpoint establishes
that every eligible Messenger send for that account is admitted to the private
ledger and uses the native path; it advances from subsequent committed service
health/checkpoint records, not only when an email happens to be sent.  Both start
no earlier than authenticated ingress enforcement or approved provider-native
capability enablement, respectively.  A cursor reset, checkpoint gap,
re-authentication, account rebinding, native-path fallback/disable, failed
confirmation operation, or retention/pruning discontinuity closes the relevant
epoch; coverage resumes only at a new post-recovery start.  It never backfills a
prior interval.  Coverage state stores no raw event, peer, provider payload, or
content.

`bidirectional=true` requires both matching evidence legs within 180 days,
same-account authenticated provenance, current coverage/watermarks, and, where
a peer-alias authority is used, authority active at query time and valid at both
evidence timestamps.
`bidirectional=false` is permissible only for an entity with at least one active
literal `has-email` peer and no active explicit peer-alias authority.  The output
is entity-wide, not peer-scoped: absent a positive result, any active alias
authority for that entity forces `unknown`/`null`, even if the entity also has a
literal peer.  The maximum peer-alias lifetime/retention is exactly 180 days, so
a half-open alias interval cannot both cover the whole rolling 180-day window and
remain active at query time; alias-derived negative evidence is deliberately not
available in v1.  For every literal peer, the private aggregate evaluates every
canonical account in the single current complete account-universe continuity
interval, not a caller-selected account.  That complete-universe interval itself
must contiguously span the entire requested 180-day window.  Every member account
must have both relevant coverage epochs contiguously span the entire requested
180-day window and have no qualifying leg for every literal peer.  Only then can
the aggregate return `false`; it is false for no additional provider/account
universe, not merely an arbitrarily chosen covered account.  A fresh recent
checkpoint alone is not full-window coverage.  If the complete universe is
absent/incomplete/expired/gapped or does not cover the requested window, a peer
is alias-authorized or unresolvable, any member capability is
disabled/unproven/unavailable, an ingress epoch is absent/gapped, either member
epoch does not cover the full window, or a watermark exceeds its owner-approved
budget, the function returns `bidirectional=null` with `freshness=unknown` or
`stale`.

This prevents an empty response from being reported as a factual “no
correspondence” conclusion.  The exact watermark and receipt-delay budgets are
provider/operations gates; they must be documented before capability enablement.

### Retention and deterministic maintenance

All correspondence rows, qualified-inbound observations (including their opaque
deduplication tokens), coverage epochs, account-universe memberships, native-send
dispatch/report fences, confirmation leases/report fences, explicit alias metadata,
and any aggregate cache are hard-deleted no later than 180 days after their own
explicit retention anchor: intent for correspondence, `received_at` for inbound
observations, epoch closure/start for coverage, universe membership issuance,
dispatch/lease expiry or report recording for broker fences, and issuance for
alias authority.  Any aggregate counter may outlive them only if it contains no
account, peer, intent, dispatch, lease, report, provider-reference, or other
linkable identifier.  Maintenance is deterministic, bounded, zero-LLM, and non-
egress.  It may mark expired accepted attempts unknown, release expired
leases/dispatches, close invalid coverage/universe epochs, and delete expired
metadata.  It may not compose a message, trigger a new email, retry an unknown
attempt, or call a provider.

An active coverage or complete-universe epoch is rolled into a fresh, bounded
interval before its 180-day retention deadline.  The successor contains only the
current rolling bounds and categorical continuity, never a historical event list,
historic membership list, or pre-window evidence; it cannot bridge a reset/gap,
member-set change, or manufacture backfill.

Coverage closure is itself a fixed, atomic Messenger write.  In addition to
`messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
bytea)`, Messenger exposes only
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
to `butler_switchboard_rw`.  Its fixed inputs are broker-derived provider,
canonical account, authenticated ingress epoch, one bounded categorical closure
code (`age_invalid`, `checkpoint_gap`, `reauth`, `rebind`, or `principal_mismatch`),
and once-captured server closure time.  It returns only `closed` or `already_closed`.
It accepts no peer, raw source event ID, provider time, content, free text, or
caller principal.  It is idempotent and executes in the same authenticated
ingress transaction that detects the condition, closing/blocking the matching
coverage epoch before the generic event path returns.  The same designated
definer/search-path/schema-qualified/`PUBLIC` revocation/minimal Messenger schema
usage/exact execute posture applies; it grants no other Messenger object access.
Migrated tests must prove every invalid-age/reset/gap/re-auth/rebind/principal-
mismatch path invokes exactly this closure and cannot leave a previous watermark
fresh.

The Messenger schedule and doctrine must explicitly identify this limited
maintenance exception so that it does not silently turn an infrastructure
staffer into a proactive domain actor.

### Pre-admission privacy and cross-chain installation

Before Switchboard invokes Messenger, trusted server routing derives an opaque
`correspondence_candidate` context from a validated native email command and
trusted approval/request lineage.  It is not a caller parameter, cannot be
spoofed by a generic notification sender, and contains no subject, body,
recipient, header, provider credential, or full envelope.  It propagates through
network, validation, timeout, and no-admission failures.  When present,
Switchboard and Messenger must convert failures to bounded categories before any
generic notification, inbox, audit, routing log, trace, metric, or caller result
can persist or return raw content or error text.  Ordinary non-candidate
notification behavior is unchanged.

The private Messenger tables may be installed in the Messenger migration chain,
but the cross-schema aggregate cannot be assumed available when either chain
runs alone.  A new migration-admin-owned idempotent post-chain activation routine
must run after every Messenger/Relationship chain completion (and after
all-migrate finalization) only when both `messenger.email_correspondence` and
`relationship.entity_facts` are present.  It validates the definer owner and
ACLs, then creates/activates the fixed function.  Messenger-first,
Relationship-first, and absent-Relationship-schema paths must leave the
aggregate unavailable/disabled and yield `unknown`, never fail startup, grant
broad privilege, use dynamic SQL, or substitute a raw table read.  Downgrade
revokes the aggregate before either dependent schema changes.

## Rollout and rollback

1. Implement and test the storage, authenticated ingress/provider-native send,
   privacy, ACL, coverage, retention, and aggregate contracts on a review branch.
   Migrations run only via the normal migration runner after owner authorization.
2. Deploy all provider/account capabilities disabled by default.  Do not seed
   data through a mailbox scan or historical backfill; coverage starts at the
   post-enable epoch and cannot support full-window negative evidence until it
   naturally spans 180 days.
3. Enable one explicitly approved provider/account only after an exact-head
   privacy/security review and provider proof test.  Monitor only bounded
   categorical counts, freshness, and coverage timestamps.
4. To disable, stop issuing reconciliation leases and return
   `freshness=unknown`, `bidirectional=null`.  Revoke aggregate execution if
   necessary; never substitute raw table access.
5. A schema downgrade revokes the function first and drops private structures
   only when empty.  Any non-empty ledger is retained for a separate
   owner-approved retention decision; no destructive cleanup is implied.

## Required owner decisions before implementation

1. **Provider owner:** Messenger-initiated provider-native send operation that
   returns an exact reference, Gmail Sent-proof operation, OAuth scope, account
   reference semantics, principal-bound dispatch/send-report/confirmation-report
   idempotency, transient provider-age assertion source and budget,
   confirmation/receipt-delay deadline, full-window coverage semantics, and
   no-scan contract.
2. **Identity/privacy owner:** active-literal `has-email` v1 decision or explicit
   alias-authority source/writer/version/revocation/expiry lifecycle, peer
   matching behavior (including validity at evidence time and the entity-wide
   alias-negative-null rule), exact canonical-account-only scope, 180-day hard
   retention, and content-free observability review.
3. **Security owner:** RFC 0006 exception, cross-chain installer/definer
   owner/search path/signature, aggregate and broker ACL sets, connector ingress
   and broker credential/principal binding, scrubbed pre-admission route-error
   contract, and migrated-database test criteria.
4. **Messenger/Switchboard owner:** server-only pre-route candidate context,
   provider-native send broker boundary, and the deliberate change that
   correspondence emails do not produce generic content-bearing
   notification/inbox/audit mirrors.
5. **Relationship owner:** aggregate tri-state consumption, receipt-time and
   coverage semantics, a separate known-entity job, and no fact/claim when data
   is stale, unknown, or insufficiently covered.
6. **Operations/product owner:** disabled-by-default rollout, canary, user
   wording for SMTP acceptance, monitoring, rollback, deployment, and live-data
   authorization.
7. **Product/operations owner:** complete canonical account-universe
   configuration source/completeness, disabled or unproven account inclusion,
   identical-member-set continuity rollover, and the 180-day negative-evidence
   hold after any member-set change, gap, or expiry.

## References

- `about/heart-and-soul/security.md` - least privilege and content handling.
- RFC 0003 - Switchboard routing and ingestion boundary.
- RFC 0004 - identity and contact resolution.
- RFC 0006 - database schema isolation; this RFC's narrow exception needs
  explicit security-owner approval.
- RFC 0010 - limited deterministic cross-butler read exception precedent; all
  reuse criteria bind the scheduled Relationship aggregate consumer above.
- RFC 0017 - owner-routing safety and provenance gates.
- `openspec/changes/true-bidirectional-email-correspondence/` - normative
  proposal, delta specs, technical design, and implementation plan.
