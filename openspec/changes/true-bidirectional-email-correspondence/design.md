## Context

Today, Messenger is the only butler with email send tools enabled.  Its shared
`EmailModule` creates an SMTP MIME message, returns a local `sent` result, and
writes a public `gmail_send` audit record containing recipient, subject, and
raw error text.  Routed `notify.v1` email delivery can also write its message
and recipient to `switchboard.notifications` and an outbound row to
`switchboard.message_inbox`.  Those content-bearing stores are neither a
private correspondence ledger nor reliable provider evidence.

The Gmail connector currently reads a bounded Sent-mail cache only to prioritize
inbound replies.  It does not persist provider-Sent proof, and the Relationship
email identity job intentionally derives a heuristic from inbound
`public.ingestion_events` recurrence because no reliable outbound signal exists.
The source-of-truth identity contract is active literal
`relationship.entity_facts` rows with `predicate='has-email'`; older
`public.contact_info` topology references are retired.

The current Gmail process owns a `CachedMCPClient` pointed only at Switchboard.
That client currently accepts just an endpoint URL and client name, while the
connector-base specification describes bearer-token scope enforcement as target
state and the ingest module describes transport authentication without exposing
the principal to the tool.  The plan therefore cannot assume a direct Gmail to
Messenger client or caller-supplied `connector_type`/`endpoint_identity` fields
are authenticated.  The reconciliation broker must establish and carry a
verified connector/account principal explicitly.

Owner Option A authorizes this design work only.  It must preserve Messenger as
the delivery-only egress owner, RFC 0006 schema isolation, the current
approval/route command boundary, and the privacy prohibition on persisting or
exposing message content, headers, attachments, raw provider responses, audit
text, or secrets through the new correspondence feature.

## Goals / Non-Goals

**Goals:**

- Prove bidirectionality only from a provider-confirmed outbound send and a
  qualifying inbound signal for the same account and peer inside 180 days.
- Keep all raw correspondence evidence private to Messenger and make
  Relationship's read surface bounded, aggregate-only, and freshness-aware.
- Define crash-safe, transactional intent recording, authenticated ingress and
  provider-native send/reconciliation, idempotency, coverage, retention, ACLs,
  test matrices, rollout, and rollback before code is written.
- Preserve a truthful result when an SMTP or provider outcome is indeterminate:
  lack of proof is `unknown`, never a negative or bidirectional claim.

**Non-Goals:**

- Mailbox scans, Sent enumeration, historical backfill, use of the existing
  Gmail Sent-ID cache as evidence, provider mutation, or any live provider call
  in this planning change.
- Persisting or deriving a body/subject/header/attachment fingerprint, content
  hash, free-text audit/error, raw provider object, credential, or opaque
  `notify.v1` envelope in the ledger, aggregate, or new observability path.
- Reusing legacy Messenger delivery tables, `switchboard.notifications`,
  `switchboard.message_inbox`, or `public.audit_log` as a ledger or evidence
  source.
- Treating caller-populated inbound envelope identity, a fresh cursor alone, an
  SMTP/RFC message identifier, or a broad Sent cache as same-account,
  full-window, or exact-reference proof.
- Altering relationship facts, inferring address aliases, or treating an
  inbound-only recurrence pattern as proof of outbound correspondence.
- Implementing code, executing DDL, changing a provider account, deploying,
  or operating on live data under this planning authorization.

## Decisions

### 1. Use one private, allowlisted ledger with a conservative state machine

The Messenger migration chain will own `messenger.email_correspondence` rather
than resurrecting the retired generic delivery-tracking tables.  Each row is
limited to an opaque intent/idempotency identifier, canonical provider account
reference, normalized bare peer address, optional provider message and thread
references, the categorical state, and lifecycle timestamps.  It has no JSONB
payload, free-text status, body/subject/header/attachment columns, provider
response, recipient display name, credential, or error text.

`unknown` is deliberately both the pre-dispatch state (identified by an absent
`dispatch_started_at`) and the conservative post-dispatch state when a process
crashes, a timeout occurs, or provider confirmation cannot arrive before its
deadline.  `accepted` means the transport/provider accepted the attempt;
`confirmed` means a capable provider has explicitly proved the identified
message is in Sent; `failed` means rejection was known before acceptance.
Only `confirmed` records can be correspondence evidence.  The state transition
is monotonic except `accepted -> unknown` when the confirmation deadline
expires; no retry can turn an indeterminate send into a fabricated failure.

This is preferred to a universal delivery queue because the current direct
adapter egress is real and the old queue was demonstrably unwired.  It is also
preferred to an audit-log extension because public audit records are
content-bearing and do not establish provider facts.

The ledger is accompanied by distinct allowlisted tables for qualified inbound
observations, per-account coverage epochs, complete account-universe memberships,
native-send dispatch/report fences, and confirmation lease/report fences.  They
retain only opaque IDs, provider/account references, normalized bare peers where
strictly required, state, and timestamps; none offers JSONB, free text, raw
events, raw provider values, or content.  An explicit provider alias authority is permitted only if
the owner approves its separate versioned lifecycle:
one provider/account/entity/peer tuple, fixed issuance/expiry, revocation,
approved source/writer, and purge by its own 180-day timer.  It is not a generic
address book and never auto-renews through correspondence.  It can qualify a
peer only while active at query time and only for evidence dated on or after its
issuance and before its expiry/revocation.  Provider account aliases are not
part of this v1: same-account proof requires exact equality of canonical account
references.

The account universe is a separate private, owner-approved configuration inventory
of every canonical provider/account through which the owner can possibly send or
receive correspondence, including disabled, unsupported, and unproven accounts.
It is never inferred from mailbox contents, a provider account listing, generic
ingestion, or backfill.  Each member record has an opaque universe epoch,
rolling `covered_from`/`covered_through`, complete-configuration checkpoint, and
categorical continuity state, but no peer, credential, content, or raw provider
data.  It is never exposed to Relationship.  A current complete universe can
roll to a bounded successor only when the set is identical and the earlier
complete interval ended without a gap; the successor carries only current
membership and its continuity interval, not a historical membership list.
Addition, removal, rebinding, expiry, incompleteness, or continuity failure
closes the interval.  Negative evidence is unavailable unless that interval
spans the entire queried window.

### 2. Commit intent before egress; use idempotency without blind resends

After approval and native-command construction but before an email provider is
called, Messenger inserts or locks the one ledger row for a server-derived,
opaque idempotency key in the same transaction that records its intended
correspondence metadata.  Failure to commit that intent blocks egress: there
must be no unrecorded attempt.

The external provider call is inherently outside that transaction.  A process
crash or timeout after it begins leaves the row `unknown`; recovery reconciles
the exact known provider reference when one exists and never sends again merely
because a worker restarted.  A retry is permitted only when the provider's
documented idempotency contract is bound to the same opaque key.  SMTP has no
such proof in this design, so it cannot be blindly retried after an ambiguous
attempt.  Concurrent workers use the unique key and row locking/lease fields
derived from timestamps to ensure one dispatch/reconciliation claimant.

For an exact-Sent capable account, Messenger must use a disabled-by-default,
Messenger-initiated provider-native send broker.  It dispatches one approved,
opaque-command-bound record; the credential-owning Gmail connector polls the
authenticated `correspondence.send.dispatch` broker tool and receives at most
that record for its derived account principal.  Gmail returns the stable provider
message/thread reference directly from that operation's response and reports it
through an authenticated `correspondence.send.report` callback bound to the
one-time dispatch ID, fence, and deterministic send-report ID.  Messenger
atomically validates and consumes the dispatch report before recording its direct
native categorical outcome; only a recorded `accepted` direct reference can be
leased for confirmation.  An identical callback returns the earlier categorical
result, while an expired, conflicting, cross-account, or altered-fence callback
fails closed.  A crash after native send but before that callback remains
`unknown` and can retry only the same report, not the provider send absent the
provider's documented idempotency contract.  Content travels transiently only
along this dedicated authenticated internal route and may not enter generic
Switchboard persistence or logs.  The connector cannot use the route for generic
egress or manufacture a dispatch.  SMTP responses, RFC message IDs, Sent cache
entries, list/search/history data, and delayed correlation can never create a
reference eligible for a confirmation lease; SMTP stays `accepted -> unknown`.

### 3. Use an authenticated Switchboard broker for exact-reference confirmation

The future provider adapter returns a typed, metadata-only outcome.  A capable
provider must supply a stable account reference plus an exact message reference
at send time, then prove that exact reference is in its Sent representation
through a bounded exact-ID metadata lookup.  It may pass only account reference,
message/thread reference,
categorical outcome, and observed timestamps to Messenger.  The connector must
not list, search, or traverse a Sent mailbox, consume an existing broad Sent
cache, read message content, or perform backfill for this purpose.

Gmail SHALL not receive a Messenger endpoint, Messenger table grant, or a
generic ledger writer.  It calls four Switchboard connector tools only:
`correspondence.send.dispatch`, `correspondence.send.report`,
`correspondence.confirmation.poll`, and
`correspondence.confirmation.report`.
The cached connector client must send an authenticated transport credential held
in the secret authority and must not log or return it.  Switchboard derives the
immutable connector type and canonical endpoint/account principal from that
credential; it rejects any absent, expired, revoked, wrong-scope, or
provider/account-mismatched caller before tool logic.  Caller arguments cannot
select another provider or account.

On a valid `send.dispatch` poll, Switchboard uses authenticated internal routing
to request at most one one-time, short-lived opaque dispatch tied to that
verified principal and private admitted intent.  Gmail sends it natively and
returns its direct categorical result only through `send.report`; Messenger alone
atomically validates its dispatch ID/fence/deterministic report ID and records
the result.  Switchboard can request a `confirmation.poll` lease only after that
commit records an accepted exact reference.  Such a lease contains its opaque ID,
fence, deterministic report ID, exact already-known reference, and allowed
timestamps; it has no body/subject/header/payload and is not an account-
enumeration mechanism.

On either report, Switchboard authenticates and derives the same principal,
validates only the transport-visible dispatch or lease shape, and forwards the
relevant ID, fence, deterministic report ID, categorical outcome, exact
reference only for `send.report`, and allowed timestamp through a dedicated
scrubbed internal route.  It does not separately consume either fence.  Messenger
is the sole atomic dispatch/lease-consume-plus-outcome writer: in one transaction
it verifies broker context, principal, dispatch-or-lease/account/reference
binding, expiry, fence, and report ID, consumes it, and records the outcome.
Retrying an identical report returns the already-recorded categorical result
without a second transition; a conflicting, expired, cross-account, or altered-
fence report fails closed.  This avoids losing a valid report if Switchboard or a
worker crashes between routing and the Messenger transaction.  Messenger never
trusts a connector-supplied identity claim.  A confirmation report can prove only
the leased exact reference; it cannot set an arbitrary ledger row `confirmed`.

The broker must not use the generic Switchboard `route()` exception/logging
path, which currently derives and persists raw exception text in
`switchboard.routing_log`.  Its dedicated internal route and outward response
must classify and scrub failures before any persistence, metric, or caller
surface.

Generic SMTP can transition an intent to `accepted`, but has no provider-Sent
proof and therefore cannot transition to `confirmed`; it becomes `unknown` at
the configured deadline.  A provider with inadequate scope, an absent exact
reference, or a stale reconciliation watermark is likewise `unknown`, not
negative evidence.

The Gmail connector remains the provider-credential owner.  It obtains a
bounded lease through the authenticated Switchboard broker, performs at most an
exact-reference metadata check for the leased provider/account pair, and reports
the typed outcome through the same broker.  This preserves the connector and
Messenger boundaries without giving Gmail, Relationship, or a generic runtime
SQL access to the ledger.  Messenger maintenance is deterministic and
non-LLM/non-egress: expire overdue confirmations and retention rows only.  Its
addition must be documented as compatible with Messenger's prohibition on
autonomous domain behavior and scheduled prompts.

### 4. Correlate only exact account and explicitly authorized peer identity

The private aggregate function receives a bounded list of Relationship entity
IDs, never an arbitrary address predicate.  It resolves a peer only when an
active literal `relationship.entity_facts` `has-email` value exactly matches
the shared email normalizer, or when a provider-specific alias authority is
explicitly recorded for that provider/account/entity under an owner-approved
configuration contract, is active at query time, and covers each qualifying
evidence timestamp (`issued_at <= evidence_at < expires_at`, with no revocation
at or before `evidence_at`).  It must never infer aliases from display names,
domains, plus-addressing, thread context, or stale/retracted/superseded facts.

The aggregate evaluates only private qualified-inbound observations, not an
arbitrary `public.ingestion_events` projection.  It never returns or copies raw
payload, normalized text, headers, event IDs, thread IDs, or an inbound-row
listing.  Same-account means the transport-authenticated canonical provider
account reference exactly equals the ledger account reference, not a textual
address guess or provider account alias mapping.

Current inbound `public.ingestion_events` cannot qualify just because their
envelope asserted an endpoint.  Before one account is enabled, Switchboard must
authenticate connector ingress, derive connector/provider/endpoint identity from
the transport, verify that binding against the canonical account, and issue an
opaque ingress epoch.  Only post-enforcement events from an unbroken epoch emit
a private qualified-inbound observation.  The projection is limited to canonical
account, normalized bare peer, epoch, and server `received_at`; it does not read
payload/raw envelope `observed_at`, headers, event IDs, threads, or content.
Resets, gaps, re-authentication, account rebinding, or identity mismatch end
coverage and exclude affected events.  The 180-day time basis is server receipt
time.  At the authenticated provider-adapter seam only, a documented provider
field may derive a strictly typed, transport-bound transient age assertion.
Switchboard validates it against the broker-derived principal, server clock, and
owner-approved maximum delay/future-skew budget, then discards it.  It is never a
generic caller or raw-envelope field, never persisted in `public.ingestion_events`
or a private correspondence record, never exposed to the aggregate, and never
used as the 180-day timestamp.  Missing, malformed, future-skewed, or over-age
assertions produce no qualified observation and invoke the fixed
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
operation with `age_invalid` before the generic event path returns.  Reset, gap,
re-authentication, rebinding, and principal mismatch use that same operation with
their respective bounded category (`checkpoint_gap`, `reauth`, `rebind`, or
`principal_mismatch`) in the authenticated ingress/control transaction that
detects them.

At the accepted-ingress transaction, Switchboard derives a non-reversible,
account-scoped opaque token from the authenticated provider's immutable source-
event key using a secret held outside the database.  It invokes a fixed,
Messenger-owned non-generic function
`messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
bytea)` in that same transaction.  Its inputs are only Switchboard-derived
provider, canonical account, normalized peer, ingress epoch, server receipt time,
and opaque token; it returns only `recorded` or `duplicate` and uses the token to
create at most one qualified observation and related coverage advance.  Its
migration-managed `SECURITY DEFINER` contract uses an explicit definer, `SET
search_path = pg_catalog`, schema-qualified body, `REVOKE ALL ... FROM PUBLIC`,
and only `USAGE ON SCHEMA messenger` plus exact function execute for
`butler_switchboard_rw`; that role receives no direct table or other Messenger-
object access.  The raw provider event ID is never retained.  The closure
operation receives only broker-derived provider/account, ingress epoch, one of
those five fixed categories, and once-captured server closure time; it returns
only `closed` or `already_closed`.  It has the same migration-managed definer,
`SET search_path = pg_catalog`, schema-qualified body, `REVOKE ALL ON FUNCTION
messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)
FROM PUBLIC`, minimal `USAGE ON SCHEMA messenger`, and exact `GRANT EXECUTE ON
FUNCTION messenger.close_qualified_email_coverage(text, text, uuid, text,
timestamptz)` only to `butler_switchboard_rw`, as the projection function.  It
has no peer, provider time, raw source ID, content, free text, or
caller-principal input.  A duplicate/retry
returns the original categorical projection result without replacing
`received_at` or advancing coverage.  If that qualified projection cannot commit,
the generic event may follow its existing contract but the correspondence path
records no evidence and keeps coverage unavailable.

### 5. Expose only a bounded aggregate with truthful freshness

RFC 0006 normally forbids cross-schema reads.  This design proposes one narrow
read-only exception: a migration-owned `SECURITY DEFINER` function in `public`
with `SET search_path = pg_catalog`, explicit schema-qualified references,
`REVOKE ALL ... FROM PUBLIC`, and `GRANT EXECUTE` only to
`butler_relationship_rw`.  It accepts no address, account, provider ID, or
unbounded selector and returns at most one fixed-shape row per supplied entity:
`entity_id`, a capped confirmed-outbound count, last confirmed-outbound time,
last qualifying-inbound time, `bidirectional` (`true`, `false`, or `null`), and
`freshness` (`fresh`, `stale`, or `unknown`).  It never exposes raw ledger rows,
addresses, account/provider references, message/thread IDs, individual state
rows, audit text, or content.

RFC 0010 permits that exception only as a database-enforced narrow reader with
a fixed batch and a material cost case.  The only consumer SHALL be a new
Relationship `email_correspondence_enrichment` handler registered in
`src/butlers/scheduled_jobs.py` and seeded exactly once daily as
`35 6 * * *` in `roster/relationship/butler.toml` with
`dispatch_mode="job"` and `job_name="email_correspondence_enrichment"`.  It
receives a maximum 100 already-resolved entity IDs, has zero-LLM behavior, and
has no MCP/API/on-demand/interactive consumer.  No Relationship or
Messenger MCP tool, dashboard/API endpoint, Switchboard route, scheduled prompt,
or LLM session may invoke the aggregate.

That declaration alone is insufficient because Relationship exposes generic
scheduling tools.  Future implementation SHALL add a scheduler-level
protected-job registry keyed by the deterministic canonical identity:
Relationship, `source='toml'`, `name="email-correspondence-enrichment"`,
`cron="35 6 * * *"`, `dispatch_mode="job"`,
`job_name="email_correspondence_enrichment"`, and configuration-declared job
arguments.  The shared scheduler seams in `src/butlers/core/scheduler.py` and
`src/butlers/core_tools/_scheduling.py` SHALL enforce that registry: reject a
generic `schedule_trigger` before dispatch, and reject `schedule_create` or
`schedule_update` before persistence when a caller targets, aliases, or mutates
the protected job.  Only trusted configuration synchronization for the fixed
TOML schedule and its due scheduler tick may dispatch it.  The rejection is a
bounded auditable category with a dedicated metric and security audit event; it
must not include job arguments, entity IDs, accounts, peers, or correspondence
metadata.  This is a future implementation seam, not a scheduler implementation
or operational authorization in this planning change.

The bounded cost comparison is explicit.  At the maximum 100 IDs, a compliant
per-entity Switchboard MCP fan-out would require one Relationship LLM session
plus up to 100 Messenger LLM response sessions, or up to 101 LLM sessions per
daily batch.  The registered deterministic job uses zero LLM sessions for the
same fixed-shape result.  A hypothetical batched MCP tool is not an equivalent
shortcut because it introduces an interactive surface; it needs a separate
contract and cannot relax this exception.

`bidirectional=true` requires both evidence legs in the same rolling 180-day
window, an active exact peer authority that covers both evidence timestamps when
an alias is used, and fresh account/provider watermarks.  Each account has metadata-only
inbound-ingress and outbound-confirmation coverage epochs with contiguous
`covered_from`/`covered_through` bounds.  Inbound bounds follow successful
committed authenticated checkpoints.  Outbound bounds begin only after an
approved enforcement checkpoint establishes that every eligible Messenger send
for the account is admitted to the private ledger/native path, then advance from
committed service-health checkpoints even if no mail is sent.  An epoch starts no
earlier than enforcement/capability enablement and closes on reset, gap, re-auth,
rebinding, native-path fallback/disable, failed proof, or retention discontinuity;
it never backfills a prior interval.  `false` is returned only when the entity
has a nonempty active literal `has-email` peer set, has no active peer-alias
authority, and a current complete account-universe continuity interval spans the
entire requested 180-day window.  For every literal peer and every canonical
account in that internally configured universe (never a caller-selected account),
both relevant account epochs must continuously cover the whole interval and no
qualifying leg may exist.  The aggregate is entity-wide rather than peer-scoped:
if there is no `true`, any active alias authority forces `null`, even where a
literal peer also exists.  Alias-derived negatives are intentionally unavailable
in v1 because the 180-day half-open authority retention interval cannot both
cover the rolling window and remain active at query time.  A recent fresh
checkpoint without complete-universe and full-account coverage is `unknown`/`null`,
not `false`.  No active literal peer, any active alias authority, an
absent/incomplete/gapped universe interval, a partial account epoch, `stale`, or
`unknown` freshness forces `bidirectional` to `null`, so empty aggregates cannot
be mistaken for negative evidence.

### 6. Retain exactly the useful metadata and fail closed on rollback

Every correspondence row, qualified inbound observation, coverage epoch,
complete account-universe membership, native-send dispatch/report fence,
reconciliation lease/report fence, provider-peer authority record, and any
aggregate cache is hard-deleted no later than 180 days after its own defined
intent, receipt, epoch, universe-membership issuance, dispatch/lease/report, or
authority issuance anchor.  The inbound observation's opaque source-
deduplication token shares its observation retention anchor.  Aggregate counters
may outlive those rows only if they are unlinkable categorical totals.  No
historical import, broad backfill, or post-expiry summary survives.  Expiry runs
deterministically with bounded batches and reports only categorical
counts/timestamps.  The aggregate's query window is also exactly 180 days.

An active coverage or complete-universe epoch rolls into a fresh bounded interval
before its 180-day retention deadline.  Its successor keeps only current rolling
coverage or membership bounds and categorical continuity, never a historical
event list, historical membership list, or pre-window evidence, and it cannot
bridge a reset/gap, member-set change, or create backfill.

Feature enablement is capability-gated per provider/account.  Disabling a
provider or aggregate path produces `unknown` freshness and no new proof; it
does not rewrite history.  A downgrade first revokes the aggregate interface.
It may drop the private schema only when it is empty; otherwise it fails closed
before destructive DDL and requires a separate owner-approved retention
decision, mirroring the `msg_003` retirement safeguard.

### 7. Activate the cross-schema aggregate only after both chains are ready

Messenger owns the private tables, while Relationship owns
`relationship.entity_facts`; their Alembic chains may run independently and in
either order.  A new migration-admin-owned idempotent post-chain activation
routine therefore runs after each Messenger/Relationship chain completion and
after all-migrate finalization.  It creates the `SECURITY DEFINER` aggregate only
after it sees both schema contracts, validates the designated definer owner and
grants, and can safely use explicit schema-qualified SQL.  Messenger-first,
Relationship-first, and missing-Relationship-schema installations remain
aggregate-disabled and return `unknown` rather than failing startup, granting a
broad role, or substituting direct table reads.  Downgrade revokes the function
before changing a dependent schema.

### 8. Separate correspondence evidence from content-bearing observability

Before route invocation, trusted server routing must derive an opaque
correspondence-candidate context from the validated native email command and
trusted request/approval lineage.  It is not a caller argument and cannot be
requested by an unrelated notification.  It intentionally excludes recipient,
subject, body, headers, credentials, and full envelope, but survives network,
validation, timeout, and no-admission paths.  The correspondence path must
replace the current direct `gmail_send` audit payload with a categorical,
content-free outcome and suppress `switchboard.notifications` and
`switchboard.message_inbox` mirrors for the candidate even before Messenger
admits an intent.  Both Switchboard and Messenger must convert errors to bounded
categories before any generic log, trace, metric, audit, route result, or
notification persistence.  Metrics and logs may use bounded
outcome/provider-capability counters only; they must not contain peer values,
identifiers, message content, raw exceptions, or provider payloads.

Existing content-bearing audit, notification, inbox, and ingestion records are
governed by their own contracts.  They are expressly excluded as evidence and
this plan does not misrepresent them as cleaned up.  If the owner means to
eliminate all pre-existing email-content persistence outside the new path, that
is a separately scoped privacy migration and an explicit owner gate.

## Risks / Trade-offs

- **[SMTP users gain no positive proof]** -> Report `accepted` then `unknown`;
  never market SMTP acceptance as bidirectionality.
- **[A provider API's send result is not actually Sent proof]** -> Require a
  provider owner to document native-send exact-reference and exact-ID Sent
  semantics and a migrated-DB/provider contract test before enabling that
  capability.
- **[A narrowly privileged function becomes a schema-isolation bypass]** ->
  Fixed signature, bounded input/result, explicit grants, definer/search-path
  tests, and no raw SELECT grant to Relationship.
- **[Address aliases create false joins]** -> Accept only active `has-email` or
  explicit peer authority that was valid for the evidence time and remains active
  at query time; no heuristic normalization beyond the shared bare-address
  normalizer or provider account alias is allowed.
- **[A crash causes a duplicate external message]** -> Do not blind-retry an
  unknown attempt; reconcile the exact reference or require provider idempotency.
- **[A connector can forge another account's confirmation]** -> Bind every
  native dispatch/send-report and confirmation lease/report to a transport-
  authenticated connector/account principal, Switchboard broker context, one-time
  opaque fence/deterministic report ID, and exact reference; atomically consume
  and record in Messenger; reject caller-provided account/provider claims and test
  forgery/retry paths.
- **[Freshness appears more certain than it is]** -> Couple every boolean to
  authenticated ingress/provider coverage intervals, a resolvable peer set, and
  alias authority coverage as well as recent checkpoints; a partial 180-day
  window returns `null`.
- **[Delayed or duplicated ingress appears newly received]** -> Validate one
  transient authenticated provider-age assertion before the ingress transaction,
  use server receipt time only after that check, and atomically deduplicate the
  qualified projection without retaining the provider event ID.
- **[Caller-populated ingress identity causes a false same-account join]** ->
  Derive connector/provider/endpoint from transport, bind it to a canonical
  account and ingress epoch, and exclude pre-enforcement or gapped records.
- **[Route failure leaks content before intent admission]** -> Carry only a
  trusted opaque candidate context before routing and categorize failures before
  generic persistence or results.
- **[Independent migration chains leave a half-installed bridge]** -> Activate
  the aggregate idempotently only after both schemas and ACLs are ready; all
  partial topology paths fail closed as unknown.
- **[Retention cleanup fails]** -> Treat aggregate freshness as unknown and
  alert with count-only telemetry; do not extend evidence indefinitely.
- **[Existing generic logs remain content-bearing]** -> The future email
  correspondence route must bypass them; broader historic remediation remains
  outside this authorization and needs its own owner decision.

## Migration Plan

1. Obtain the owner gates below, then land the RFC/OpenSpec implementation
   package with source and migrated-database tests before any provider capability
   is enabled.
2. Apply the Messenger private migration through the ordinary runner only, then
   let the migration-admin-owned post-chain activation routine run after each
   relevant chain/all-migrate finalization only after both Messenger and
   Relationship schemas are present and ACLs verify.  It creates no historical
   rows and leaves a partial topology aggregate-disabled.
3. Ship the correspondence path behind disabled-by-default provider/account
   capability flags.  A provider becomes eligible only after native-send exact
   reference and Sent confirmation behavior, authenticated ingress binding,
   send-report/confirmation-report replay behavior, transient age validation,
   peer-authority timing, and full-window coverage budget pass contract tests.
4. Enable a single approved provider/account after an explicit privacy and
   operations review.  Observe only unlinkable categorical
   freshness/count/coverage telemetry; do not scan or backfill mail to seed
   evidence.
5. To roll back, disable capability flags and revoke the aggregate function so
   Relationship returns `unknown`.  Preserve rows until scheduled 180-day
   expiry.  A schema downgrade refuses non-empty data and requires a separate
   owner retention decision rather than destroying evidence silently.

## Open Questions

- **Provider owner gate:** Which Messenger-initiated Gmail native-send operation
  returns the exact stable reference, which operation/scope proves that same
  reference is in Sent without enumeration, what dispatch/send-report and
  confirmation-report idempotency it supports, and what confirmation/receipt-
  delay budgets are defensible?
- **Identity/privacy owner gate:** What explicit, versioned provider alias
  authority can map a peer alias to an entity without inferred address
  equivalence, who may create/revoke it, and what fixed expiry/purge and
  evidence-time rules prevent it from becoming a general address book?  In this
  v1 an active alias supports a positive result only and forces entity-wide null
  for a negative result.  Provider account aliases are deliberately out of scope.
- **Security owner gate:** Approve authenticated ingress/broker principal
  binding, the pre-route candidate scrub boundary, the proposed RFC 0006
  exception, post-chain function owner/signature, grants, and operational access
  review.
- **Data-retention owner gate:** Confirm the 180-day hard-delete/rollover timer
  applies to every metadata class and no linkable aggregate/cache survives it.
- **Product/operations owner gate:** Approve disabled-by-default rollout,
  freshness budget, no-SMTP-proof UX wording, monitoring, rollback runbook, and
  the complete account-universe configuration/continuity contract required for
  any negative result.
- **Scope gate:** A broader removal of existing email content from generic
  audits/notifications/ingestion is not implied by this change and requires a
  separately approved privacy migration.
