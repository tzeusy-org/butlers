# Implementation Plan: True Bidirectional Email Correspondence

**Status:** Planning packet only.  No task in this file has been executed by
`bu-9wzkl`; implementation, migration execution, provider operations, mailbox
scans, backfill, deployment, and live data work remain forbidden until their
listed owners authorize them.

## Objective

Replace Relationship's inbound-only, multi-day email recurrence approximation
with a privacy-safe, provider-confirmed correspondence signal.  The signal is
true only when Messenger has provider-Sent confirmation for an outbound email
and an inbound email correlates to the same account and resolved peer within
180 days.  SMTP acceptance alone is operationally useful but never a positive
bidirectional signal.

## Source evidence and constraints

| Current path | Verified source evidence | Planning consequence |
|---|---|---|
| Outbound email | `src/butlers/modules/email.py` builds SMTP MIME messages, returns `sent`, and writes recipient/subject/raw-error audit data; it returns no Gmail message/thread reference. | Commit a private intent before egress, return categorical results, remove correspondence-path content mirrors, and add a disabled-by-default Messenger-native send prerequisite before any lease can exist. |
| Routed delivery | `src/butlers/core_tools/_routing.py` constructs native email commands; `roster/switchboard/tools/notification/deliver.py` logs generic notifications and outbound inbox rows. | Preserve the native command/approval boundary but derive an opaque server-only correspondence candidate before route invocation and keep it through failure/admission. |
| Gmail Sent information | `src/butlers/connectors/gmail.py::_fetch_sent_message_ids` broadly scans a bounded Sent window into RAM for priority assignment. | Do not reuse it.  Reconciliation can inspect only an already-known exact provider message reference. |
| Connector transport and broker errors | `src/butlers/connectors/mcp_client.py::CachedMCPClient` currently takes only an endpoint URL/client name; `roster/switchboard/tools/routing/route.py` persists generic raw exception text in `routing_log`. | Add a scoped authenticated Switchboard broker transport and a dedicated categorical/scrubbed correspondence route; do not give Gmail direct Messenger access or reuse generic route errors. |
| Inbound account/time provenance | Gmail currently submits caller-filled endpoint identity through `CachedMCPClient`; `ingest_v1` persists it while raw provider `observed_at` remains in excluded envelope data and `public.ingestion_events` records server `received_at`. | Create qualified inbound metadata only after account-bound transport authentication; validate a transient adapter-derived provider-age assertion without persisting it, use server receipt time, and atomically deduplicate the private projection so retries cannot refresh evidence. |
| Existing email identity job | `run_email_identity_enrichment` scans unresolved inbound senders and parks `has-email` link proposals. | Preserve it as inbound-only discovery; add a separate known-entity aggregate consumer rather than changing its input/output contract. |
| Migration topology | Butler migration chains run independently; `messenger` and `relationship` can be applied in either order. | Keep private tables in Messenger and activate the cross-schema aggregate idempotently only after both schemas/ACLs verify. |
| Identity enrichment | `src/butlers/modules/contacts/email_identity_matching.py` and `roster/relationship/jobs/relationship_jobs.py` infer recurrence from inbound `public.ingestion_events`. | Relationship must consume only a bounded aggregate; no direct Messenger query and no inbound-only positive claim. |
| Identity authority | `openspec/specs/relationship-facts/spec.md` makes active literal `relationship.entity_facts.has-email` canonical. | Match exact normalized peers only, unless an approved provider-specific alias authority exists. |
| Existing tracking | `roster/messenger/migrations/003_retire_unwired_delivery_tracking.py` retired the generic queue and fails closed on non-empty rollback/drop. | Do not resurrect that queue.  Mirror its empty-only destructive rollback safeguard. |

## Invariants that implementation must preserve

1. **Privacy allowlist:** New correspondence persistence contains only opaque
   intent/key, provider/account reference, normalized bare peer, optional
   provider message/thread references, categorical state, and timestamps.
   It contains no message content, subject, headers, attachments, raw provider
   object, audit/error text, secret, display name, or content-derived hash.
2. **Evidence threshold:** `confirmed` is the only outbound evidence state;
   it requires a stable exact provider reference returned directly by an enabled
   Messenger-native provider send and proof that that reference is in Sent.
   `accepted` is not confirmation, delivery, receipt, or correspondence.
3. **Identity threshold:** A peer resolves only through an active literal
   `has-email` exact match after the shared normalizer, or through an explicit
   provider peer-alias authority approved by the identity/privacy owner that is
   active at query time and valid at every qualifying evidence time under the
   half-open `[issued_at, expires_at)` interval, with revocation effective at
   `revoked_at`.  Canonical provider account references must match exactly;
   account aliases are out of scope for v1.
4. **Isolation:** Relationship cannot select/list `messenger` correspondence
   data.  It gets one aggregate row per bounded input entity, never an address,
   account, provider reference, raw event, or raw ledger row.
5. **Honesty:** A missing/stale capability or watermark produces
   `freshness=unknown|stale` and `bidirectional=null`; empty is not absence
   unless contiguous full-window coverage proves it.
6. **Coverage honesty:** `false` requires a nonempty active literal `has-email`
   peer set, no active peer-alias authority for the entity, an internally
   owner-approved complete account-universe continuity interval spanning the full
   180-day window, and contiguous authenticated inbound and native-provider
   outbound coverage epochs over that window for every member account and every
   literal peer.  The aggregate never selects one account from a multi-account
   universe.  Alias-derived negatives are unavailable in v1; any active alias
   authority forces `null` absent a `true`.  A recent checkpoint alone produces
   `null`, never a negative conclusion.
7. **Retention:** Every correspondence-specific row, inbound observation and
   source-deduplication token, coverage epoch, native-send dispatch/report fence,
   confirmation lease/report fence, alias authority, complete account-universe
   membership, and cache is hard-deleted by its own explicit no-later-than-180-day
   anchor; the aggregate window is the same 180 days.
8. **No broad provider work:** This planning packet performs no provider
   operation.  Future reconciliation has no mailbox scan, Sent listing, search,
   history traversal, backfill, content fetch, or provider mutation; its sole
   authorized future provider write is the explicitly approved native egress
   send that obtains the exact reference directly.
9. **Ingress provenance:** An inbound event qualifies only from a
   transport-authenticated, canonical-account-bound ingress epoch; caller-filled
   envelope identity and pre-enforcement history are not evidence.  A typed,
   transient provider-age assertion is validated at the authenticated adapter
   seam but never persisted or used as evidence time; one opaque account-scoped
   source-deduplication token atomically prevents a replay from refreshing server
   receipt time or coverage.
10. **Failure privacy:** Trusted routing derives an opaque correspondence
   candidate before route invocation, so validation/network/no-admission failures
   cannot create generic content/error mirrors.

## Proposed state model and interfaces

### Private ledger

Create `messenger.email_correspondence` in
`roster/messenger/migrations/004_email_correspondence.py`.  The migration must
use explicit column allowlists rather than a JSONB catch-all.

| Field group | Allowed values | Purpose |
|---|---|---|
| Opaque identity | `id`, server-derived `idempotency_key` | One logical send across approval, retry, crash recovery, and reconciliation. |
| Routing identity | `provider`, canonical `account_ref`, normalized bare `peer_address` | Same-account/peer proof; never exposed to Relationship. |
| Provider evidence | nullable `provider_message_ref`, nullable `provider_thread_ref` | Exact-reference proof only; no raw provider object. |
| State | `unknown`, `accepted`, `confirmed`, `failed` | Conservative lifecycle, constrained by SQL and service transitions. |
| Timestamps | intent, dispatch-started, accepted, confirmed, failed, unknown, confirmation-deadline, lease-until, expires | Idempotency, recovery, freshness, and 180-day hard retention. |

No generic free-text `error`, `status`, `metadata`, `provider_response`,
`request_envelope`, `message_content`, `subject`, or `recipient` column is
permitted.  The `peer_address` is the normalized bare RFC-5322 address and is
private metadata, not generic notification content.

Add separate explicit-allowlist private tables for: qualified inbound
   observations (canonical account, normalized peer, opaque authenticated ingress
   epoch, server `received_at`, non-reversible account-scoped source-deduplication
   token, expiry); account coverage epochs (canonical account, coverage kind,
   opaque epoch, contiguous bounds, committed checkpoint time, categorical
   continuity, closure/expiry); complete account-universe memberships
   (canonical account, opaque universe epoch, rolling `covered_from`/
   `covered_through`, last complete-configuration checkpoint, categorical
   continuity, completeness closure/expiry, and no peer/content); native-send
   dispatch/report fences (opaque dispatch
   ID, fence, deterministic send-report ID, principal/intent binding, categorical
   result, direct exact reference only when reported, timestamps); confirmation
   leases/report fences (opaque ID, fence, deterministic report ID, principal
   binding, exact known reference, categorical outcome/timestamps); and, only if
   approved, versioned explicit peer-alias authority
   (provider/canonical-account/entity/normalized peer, fixed source, issued,
   expires, revocation, purge).  None may contain JSONB, free text, raw event,
   provider payload, content, secret, or generic audit/error material.  Alias
   authority has its own fixed 180-day issuance/purge anchor, cannot auto-renew
   from correspondence, and must be valid at both evidence and query time under
   the half-open `[issued_at, expires_at)` interval.  The account universe is an
   owner-approved static configuration inventory, never a mailbox/provider-list
   discovery or backfill.  It rolls only when the member set is identical and
   continuity is unbroken; a member-set change, incompleteness, expiry, or gap
   starts a new interval and prevents negative evidence until the new interval
   itself spans 180 days.

The proposed service API is deliberately typed and content-free after dispatch
admission:

```python
class CorrespondenceStore:
    async def admit_intent(self, *, provider: str, account_ref: str,
                           peer_address: str, idempotency_key: UUID,
                           confirmation_deadline_at: datetime) -> CorrespondenceIntent: ...
    async def record_provider_outcome(self, *, intent_id: UUID,
                                      outcome: ProviderOutcome) -> CorrespondenceIntent: ...
    async def issue_native_send_dispatch(
        self, *, verified_principal: ConnectorPrincipal
    ) -> NativeSendDispatch | None: ...
    async def record_fenced_native_send_report(
        self, *, broker_context: BrokerContext, dispatch_id: UUID, fence: UUID,
        send_report_id: UUID, outcome: ProviderOutcome
    ) -> CategoricalBrokerResult: ...
    async def issue_broker_lease(self, *, verified_principal: ConnectorPrincipal
                                 ) -> ConfirmationLease | None: ...
    async def record_fenced_broker_report(
        self, *, broker_context: BrokerContext, lease_id: UUID, fence: UUID,
        report_id: UUID, outcome: ProviderOutcome
    ) -> CategoricalBrokerResult: ...
    async def record_authenticated_inbound(
        self, *, verified_principal: ConnectorPrincipal,
        received_at: datetime, normalized_peer: str,
        source_dedup_token: bytes
    ) -> CategoricalIngressResult: ...
    async def expire_and_prune(self, *, now: datetime, limit: int) -> MaintenanceCounts: ...
```

`ProviderOutcome` is restricted to a category, known account/reference values,
and timestamps.  It cannot carry a payload or error string.  The email body,
subject, headers, and credentials remain in-memory only at the boundary where a
provider client needs them.  `issue_native_send_dispatch` returns at most one
opaque dispatch bound to its broker-derived principal; `record_fenced_native_send_report`
is Messenger's sole atomic dispatch-consume-plus-direct-outcome writer.  Only its
recorded accepted exact reference can later be leased.  `issue_broker_lease`
returns at most one opaque confirmation lease bound to its broker-derived
principal; it cannot enumerate accounts or rows.  `record_fenced_broker_report`
is Messenger's sole atomic confirmation-lease-consume-plus-outcome writer.  An
identical report returns its prior categorical result; a conflict, expiry,
principal/account/reference mismatch, or altered fence fails closed without a
state change.

### State transitions

```text
transactional intent admission -> unknown (dispatch_started_at is null)
unknown + dispatch begins       -> unknown (dispatch_started_at is set)
known pre-acceptance rejection  -> failed
known transport/provider accept -> accepted
exact provider Sent proof       -> confirmed
uncertain result/deadline       -> unknown
```

`unknown` intentionally covers both pending and indeterminate attempts without
adding a misleading synthetic state.  `confirmed` is terminal; `accepted` may
only become `unknown` on deadline expiry.  The implementation must use a
unique opaque idempotency key plus row lock/lease to prevent concurrent sends.
If a process dies after egress begins, it must never replay the external send
unless the provider's documented idempotency contract accepts the same stored
key.  Otherwise it reconciles an existing exact reference or remains unknown.

### Provider reconciliation boundary

Keep Gmail credential ownership in `src/butlers/connectors/gmail.py`, but add
an explicitly enabled, Messenger-initiated provider-native send capability
before reconciliation exists.  After transactional admission, Messenger creates
a one-time opaque command bound to the trusted native command/approval lineage;
authenticated Gmail polls `correspondence.send.dispatch`, and Switchboard derives
its account principal then returns at most that command on the dedicated route.
Gmail returns the stable exact message/thread reference directly from its
documented native send response through `correspondence.send.report`, carrying
only its opaque dispatch ID/fence/deterministic send-report ID, categorical
result, direct reference when present, and allowed timestamp.  Messenger atomically
validates/consumes that dispatch report and records `accepted`, `failed`, or
`unknown` before any confirmation poll can issue.  Identical callbacks return the
earlier categorical result; stale/conflicting/cross-account/altered-fence callbacks
fail closed.  A crash after native send can retry only the same report and cannot
re-send without the provider's documented idempotency contract.  Gmail cannot
initiate generic egress or synthesize a reference from SMTP, an RFC Message-ID,
`_sent_ids_cache`, list/search/history, or later correlation.  The transient
subject/body/recipient route must bypass generic notifications, inboxes, audits,
logs, traces, errors, and retry records.

The native result contains only the exact reference, allowed timestamps, and a
categorical acceptance/rejection state.  Messenger records `accepted` when known
and permits exact-reference reconciliation; this Option A packet does not treat a
native send response as Sent proof.  It never persists a provider response.

Only a Messenger-recorded accepted native-send reference may become
reconciliation-eligible.  Gmail uses all four authenticated tools
`correspondence.send.dispatch`, `correspondence.send.report`,
`correspondence.confirmation.poll`, and `correspondence.confirmation.report`
through a modified `CachedMCPClient` (or its framework boundary) that attaches a
secret-authority managed scoped credential without logging it.  Switchboard derives
the immutable connector/account principal and exposes at most one opaque
confirmation lease ID/fence/deterministic report ID for its exact known reference.
It forwards reports via a dedicated scrubbed route but does not consume a dispatch
or lease itself.  Messenger atomically validates broker context/principal/the
relevant dispatch-or-lease/fence/report ID/reference, consumes it, and records the
categorical outcome; an identical retried report returns the recorded categorical
result, while stale/conflicting/cross-account reports fail closed.  Gmail can use
one documented exact-reference metadata operation to prove Sent membership.  It
must not call `users.messages.list`, perform `q=in:sent`, use history traversal,
populate from `_sent_ids_cache`, read a body/header for reconciliation, backfill,
or mutate Gmail beyond the separately approved native send.

For SMTP, `_smtp_send` can return `accepted` but no provider reference capable
of Sent proof.  It is never leased and transitions to `unknown` after its
confirmation deadline.  The UI/API wording must say `accepted`, not `sent`,
`delivered`, or `bidirectional`.

### Aggregate-only Relationship interface

The migration-admin post-chain activation routine creates this conceptual
interface only after both dependent schemas and ACLs exist:

```sql
public.relationship_email_correspondence_summary(entity_ids uuid[])
RETURNS TABLE (
  entity_id uuid,
  confirmed_outbound_count smallint,
  last_confirmed_outbound_at timestamptz,
  last_qualifying_inbound_at timestamptz,
  bidirectional boolean,
  freshness text
)
```

The function rejects an empty/oversized (>100) or duplicate entity vector.  It
caps `confirmed_outbound_count` at 3, returns only values belonging to supplied
IDs, and reads only Messenger's private qualified-inbound observations and
coverage epochs, not historical `public.ingestion_events`, raw envelopes, or
generic inbox/audit stores.  Those observations exist only after Switchboard
authenticates the ingress transport, derives the canonical account/epoch, and
uses a once-captured server `received_at` as the 180-day timestamp.  A transient,
authenticated provider-age assertion may reject a delayed/future-skewed event at
the adapter seam but is discarded before this function can read it.  A private
unique opaque source-deduplication token makes retry return the original result
without changing receipt time or coverage.  The function resolves the inbound
canonical account and peer without returning either.  The only permitted peer
join is an active literal `relationship.entity_facts` `has-email` exact match
after the existing shared normalizer, unless an owner-approved explicit provider
peer-alias authority is active at query time and valid at every qualifying
evidence timestamp under the half-open `[issued_at, expires_at)` interval.
Canonical account references must match exactly; account alias mapping is out of
scope.

The function is an RFC 0006 exception, not a general data bridge:

```sql
CREATE FUNCTION public.relationship_email_correspondence_summary(uuid[])
...
SECURITY DEFINER
SET search_path = pg_catalog;
REVOKE ALL ON FUNCTION public.relationship_email_correspondence_summary(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.relationship_email_correspondence_summary(uuid[])
  TO butler_relationship_rw;
```

The migration/install sequence must use explicit schema-qualified names in the
function body, verify function ownership, and grant no table privilege to
Relationship, connector, dashboard, or `PUBLIC` roles.  Because Messenger and
Relationship chains migrate independently, Messenger's migration creates only
private tables.  Add a migration-admin-owned idempotent post-chain activation
routine (for example in `src/butlers/migrations.py`, called after each relevant
chain in `src/butlers/lifecycle.py` and after all-migrate finalization) that
creates/enables the function only after it verifies both Messenger tables and
`relationship.entity_facts`, the designated definer owner, and ACLs.
Messenger-first, Relationship-first, and missing-schema cases keep the aggregate
disabled and return `unknown`, without dynamic SQL, startup failure, broad grants,
or raw-table fallback.

Add a new bounded known-entity correspondence enrichment job beside, rather than
inside, `roster/relationship/jobs/relationship_jobs.py::run_email_identity_enrichment`.
The existing job discovers unresolved senders and parks link proposals; it stays
inbound-only and is not correspondence proof.  The new job batches already-
resolved entity IDs through the aggregate and consumes only the fixed result.  It
must not use raw ledger rows or existing content-bearing Switchboard messages as
a fallback.

### Freshness and 180-day truth table

| Condition | `freshness` | `bidirectional` |
|---|---|---|
| Confirmed outbound and qualified inbound, exact same canonical account/peer, any alias authority active at query time and valid at both evidence times under `[issued_at, expires_at)`, both authenticated coverage/watermarks within approved budgets, both timestamps in 180d | `fresh` | `true` |
| A nonempty literal `has-email` peer set is resolvable now; no active peer-alias authority exists for the entity; one current complete account-universe continuity interval spans the entire 180d window; every member account's two coverage epochs span it; and no qualifying leg exists for every literal peer | `fresh` | `false` |
| Provider disabled, unsupported, failed, or lacking exact Sent proof | `unknown` | `null` |
| Provider/account or inbound watermark older than its approved budget, age assertion invalid, ingress epoch gap/re-auth/reset, duplicate projection uncertainty, or coverage shorter than 180d | `stale` or `unknown` | `null` |
| Peer lacks an active literal fact; any active alias authority exists absent true; or the complete universe is newly minted/incomplete/gapped/member-changed/partial | `unknown` | `null` |

Maintenance runs in bounded deterministic batches.  It marks expired accepted
confirmation attempts unknown, releases expired leases, closes invalid coverage
or complete-universe epochs, rolls a still-active coverage or unchanged complete-
universe epoch into a fresh bounded current-window record without historic
evidence or historic membership, and hard-deletes each correspondence record,
qualified inbound observation, coverage epoch, complete account-universe
membership, alias authority, lease/report fence, native-send dispatch/report
fence, source-deduplication token, and cache by its own no-later-than-180-day
anchor.  It has no LLM call, no content
processing, no external send, and no provider call.  Register it in
`src/butlers/scheduled_jobs.py` and `roster/messenger/butler.toml`; update
Messenger doctrine to identify it as a non-domain, deterministic exception to
the staffer's no-autonomous-behavior posture.

## File-by-file implementation sequence

### Phase 0 - authority and contract freeze

1. Get written approval for the provider proof mechanism, alias authority,
   security-definer exception, retention, rollout, and communications wording.
2. Re-read RFC 0023 and this OpenSpec change after those decisions; update the
   delta specs before writing code if they differ.
3. Create no provider calls, database rows, migrations, or feature flags during
   this planning bead.

### Phase 1 - storage and ACLs

1. Create `roster/messenger/migrations/004_email_correspondence.py`.
   - Add the private allowlisted ledger, qualified-inbound observation, coverage
     epoch, complete account-universe membership, native-send dispatch/report
     fence, confirmation lease/report fence, and optional explicit
     peer-alias-authority tables with their separate retention anchors; use the
     existing `msg_003` empty-only destructive guard for downgrade.  Configure
     the complete account universe only through an owner-approved static
     configuration contract; do not enumerate provider accounts or mail to build
     it.
   - Add only the two fixed Messenger-owned `SECURITY DEFINER` functions
     `messenger.record_qualified_email_ingress(text, text, text, uuid,
     timestamptz, bytea)`, executable only by `butler_switchboard_rw`.  Its inputs
     are only Switchboard-derived provider, canonical account, normalized peer,
     authenticated ingress epoch, once-captured server receipt time, and opaque
     source-deduplication token; it returns only `recorded` or `duplicate`.  It
     has a migration-managed designated owner, `SET search_path = pg_catalog`,
     explicit schema-qualified references, `REVOKE ALL ON FUNCTION
     messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
     bytea) FROM PUBLIC`, and only `GRANT USAGE ON SCHEMA messenger` plus exact
     `GRANT EXECUTE ON FUNCTION ...` to `butler_switchboard_rw`.  That role gains
     no other Messenger object access; connector, dashboard, Relationship, and
     other butler roles get neither schema usage nor execute nor direct table
     access.  It does no raw-table grant, no SELECT, no content/raw event ID
     handling, and inserts at most once under a private unique token.  Its
     invocation joins the accepted-ingress transaction; failure leaves that event
     unqualified and closes/blocks coverage rather than falling back to a best-
     effort projection.
   - Add `messenger.close_qualified_email_coverage(text, text, uuid, text,
     timestamptz)`, also executable only by `butler_switchboard_rw`.  Its fixed
     inputs are broker-derived provider, canonical account, authenticated ingress
     epoch, one checked categorical code (`age_invalid`, `checkpoint_gap`,
     `reauth`, `rebind`, or `principal_mismatch`), and once-captured server
     closure time; it returns only `closed` or `already_closed`.  It has the same
     designated owner, `SET search_path = pg_catalog`, explicitly
     schema-qualified body, `REVOKE ALL ON FUNCTION
     messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)
     FROM PUBLIC`, minimal `USAGE ON SCHEMA messenger`, and exact `GRANT EXECUTE
     ON FUNCTION messenger.close_qualified_email_coverage(text, text, uuid, text,
     timestamptz)` only to `butler_switchboard_rw`.  It accepts no peer, raw
     source ID, provider time, content, free text, or caller-principal input;
     records no generic error; is idempotent; and atomically closes/blocks the
     matching coverage epoch in the authenticated ingress/control transaction.
   - Do not create the cross-schema Relationship aggregate in this chain, infer a
     peer alias, or create a provider account-alias mapping.
2. Add a migration-admin-owned idempotent post-chain activation routine in
   `src/butlers/migrations.py`, invoked after relevant chains in
   `src/butlers/lifecycle.py` and at the end of `src/butlers/cli.py::_migrate_all`.
   It uses explicit `to_regclass`/privilege checks safely, creates the fixed
   security-definer aggregate only after both Messenger and
   `relationship.entity_facts` contracts exist, and otherwise leaves it
   disabled/unknown without dynamic SQL or broad grants.  Downgrade revokes the
   function before dependent schema changes.
3. Add `roster/messenger/tests/test_email_correspondence_migration.py` and
   `tests/config/test_email_correspondence_acl_integration.py`.
   - Run a real PostgreSQL migration chain.
   - Assert no JSONB/free-text/content fields, direct table-select denial for
     Relationship, no connector/dashboard/public execute, exact narrow ingress-
     projection/coverage-close function execute only for Switchboard,
     aggregate/ingress-function designated owner, fixed signatures, hardened
     search path, schema-qualified body, caller-
     controlled search-path resistance, minimal Messenger schema-usage/no-other-
     object-access grant posture, output bound, and 180-day anchors for every
     private record.
   - Exercise Messenger-first, Relationship-first, missing-Relationship-schema,
     post-both-chains activation, and downgrade paths; partial topology must not
     fail startup or enable raw-table fallback.  Include first/duplicate ingress
     projection in a committed transaction, uncertain retry, immutable receipt
     time, no duplicate coverage advancement, and every invalid-age/checkpoint-
     gap/re-auth/rebind/principal-mismatch close operation.  Assert that no such
     close leaves a prior inbound watermark usable as fresh and that a complete
     account-universe rollover retains continuity only for an identical member
     set.

### Phase 2 - Messenger admission and direct egress

1. Add `src/butlers/modules/email_correspondence.py` (or an equally focused
   Messenger-only service with the same typed boundary).
2. Modify `src/butlers/modules/email.py`.
   - Construct MIME content only after ledger admission.
   - Replace `write_audit_entry(... {to, subject}, error=str(exc))` for the
     correspondence route with a content-free categorical outcome.
   - Return a typed outcome that does not echo recipient/subject or raw error.
   - Keep SMTP as `accepted -> unknown` and prohibit lease issuance for SMTP,
     RFC Message-ID, Sent-cache, list/search/history, or post-hoc references.
3. Modify `src/butlers/core_tools/_routing.py` to derive both a stable opaque
   key and a non-caller-spoofable opaque correspondence-candidate context from
   trusted server request/approval lineage before Switchboard route invocation.
   Pass it through immediate/replayed native commands and validation/network/
   no-admission failure paths without saving the whole envelope or email fields.
   Convert failures to bounded categories before existing generic raw-error
   logging/result code.
4. Add the disabled-by-default Messenger-native provider send service.  After
   admission it issues a one-time opaque command through Switchboard and accepts
   the direct exact reference only through a fenced, broker-derived
   `send.report` callback for that dispatch.  It atomically records the native
   categorical outcome before any confirmation lease exists, makes identical
   callbacks idempotent, fails stale/conflicting reports closed, and does not give
   Gmail generic egress authority.  Its transient content route is never written
   to generic notifications, inboxes, audits, logs, traces, or retry state.
5. Add `tests/modules/test_email_correspondence.py` and routing
   tests beside the existing approval-route tests.  Cover transactions,
   duplicate/concurrent admission, crash ordering, SMTP acceptance, rejection,
   timeout, deadline transition, idempotency-safe retry, pre-route candidate
   validation/network/no-admission/provider-error privacy, native-dispatch/send-
   report binding and replay/crash order, confirmation lease/report binding, and
   SMTP/non-native reference lease denial.

### Phase 3 - suppress generic content mirrors

1. Modify `roster/switchboard/tools/notification/deliver.py` and `log.py`.
   - The server derives the opaque correspondence-candidate before route
     invocation from trusted native command/approval lineage; no caller can
     request it and it carries no email fields.
   - For that candidate, including validation/network/timeout/no-admission
     failures, do not create a new `switchboard.notifications` record with email
     content/recipient, an outbound `switchboard.message_inbox` mirror, or a
     generic raw error/envelope log.  Classify errors before persistence or
     return. Preserve ordinary non-email notification behavior.
2. Add `roster/switchboard/tests/test_notification_correspondence_privacy.py`.
   Test success, route-validation failure, route timeout/network failure,
   no-admission failure, provider-error capture, and non-email cases; scan
   captured database/log/metric/result values to prove no content, recipient,
   raw exception, or full envelope is materialized by the candidate path.

### Phase 4 - authenticated Switchboard broker, ingress, and Gmail capability

1. Modify `src/butlers/connectors/mcp_client.py` (or its safe framework boundary)
   to attach the secret-authority-managed scoped transport credential without
   logging/returning it.  Register dedicated tools in
   `src/butlers/core_tools/_switchboard.py` and add a focused broker, for example
   `roster/switchboard/tools/connector/correspondence.py`.  It derives connector/
   provider/endpoint/account principal server-side, registers authenticated
   ingress epochs, and offers only `correspondence.send.dispatch`,
   `correspondence.send.report`, `correspondence.confirmation.poll`, and
   `correspondence.confirmation.report`.
2. Give the broker a dedicated scrubbed internal route in
   `roster/switchboard/tools/routing/route.py`; do not use the generic path that
   persists `str(exc)`.  The broker forwards a dispatch-or-lease ID/fence/
   deterministic report ID but does not consume either separately from Messenger's
   atomic outcome transaction.  `send.report` can carry the direct exact reference
   only for its own dispatch; `confirmation.report` can refer only to a lease
   created after accepted reference recording.  Missing/revoked/expired/wrong-
   scope/cross-account credentials, stale/conflicting reports, and route errors
   become bounded categories.
3. Extend `src/butlers/connectors/gmail.py` only after a provider owner writes
   the native-send exact-reference proof, exact-ID Sent proof, scope, canonical
   account, receipt-delay, and coverage decisions into configuration/spec/docs.
   Behind disabled-by-default account capability, Gmail accepts only the
   Messenger-issued native dispatch for its verified principal, returns the
   direct stable send reference through its fenced `send.report`, leases at most
   one recorded accepted exact reference, uses the documented exact metadata
   operation, and reports a categorical confirmation result.
4. Add authenticated ingress handling at the Switchboard ingestion boundary so
   a private qualified-inbound observation and coverage epoch are created only
   after principal/account verification and committed checkpoint.  At the
   credential-bound provider-adapter seam, derive a strictly typed transient
   provider-age assertion from the documented provider event-time field; validate
   it against the server clock and approved delay/future-skew budget, discard it,
   and never read or persist raw-envelope `observed_at`.  In the same qualifying
   accepted-ingress transaction, derive an account-scoped non-reversible HMAC-like
   source-deduplication token and invoke the fixed
   `messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
   bytea)` function;
   duplicates return their original categorical result without changing server
   `received_at` or coverage.  On invalid age, checkpoint gap, re-authentication,
   rebinding, or principal mismatch, atomically invoke
   `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
   with its exact fixed category before the generic ingress/control path returns;
   no valid path may leave an old coverage watermark fresh.  Exclude all
   pre-enforcement historical events and reset/gap/re-auth/rebinding periods.
   Add native-outbound coverage checkpoints
   only after enforced ledger/native-path admission for every eligible Messenger
   send, close them on fallback/disable/gap, and advance them from committed
   health checkpoints even when no email is sent.
5. Add `tests/connectors/test_gmail_correspondence_confirmation.py` and focused
   Switchboard broker/ingress tests with fakes.  Assert no list/search/history/
   backfill or reconciliation-mutation method is called beyond the approved
   native send, `_sent_ids_cache` is irrelevant, raw response is discarded, no
   credential is logged, account/reference mismatch is rejected, invalid
   principal cannot dispatch/send-report/lease/confirmation-report, identical
   send and confirmation reports are idempotent, conflicting/expired reports fail
   closed, a native-send crash cannot create a resend, direct Messenger access is
   denied, historical envelope identity cannot qualify, transient age assertions
   never persist, stale/future/absent assertions fail closed, and accepted-ingress
   replay cannot replace receipt time or advance coverage.

### Phase 5 - aggregate and Relationship behavior

1. Implement the aggregate query with its 180-day predicate, canonical account
   mapping from authenticated qualified-inbound records, server-receipt time,
   current active `has-email` lookup, optional approved peer-alias authority that
   is active at query time and valid at every evidence time under half-open
   `[issued_at, expires_at)`, count cap, coverage epochs, and freshness tri-state.
   Require exact canonical account equality;
   no provider account aliases.  Return `null`/unknown when no nonempty exact
   literal peer set resolves, when any active peer-alias authority exists for the
   entity absent a `true`, or when a private owner-approved complete
   account-universe continuity interval does not span the whole window.  For a
   `false`, quantify every literal peer across every member account in that
   complete internal universe; each member needs both coverage epochs over the
   whole window and no qualifying leg.  Never select an account from the
   entity-ID input, infer the universe from provider/mailbox data, or issue an
   alias-derived negative.
2. Add a separate bounded known-entity correspondence enrichment job beside
   `roster/relationship/jobs/relationship_jobs.py::run_email_identity_enrichment`.
   The new job asks only for resolved entity-ID batches and never writes raw
   correspondence metadata into Relationship facts/logs.  Preserve the existing
   unresolved-sender discovery/proposal job unchanged and inbound-only.
3. Add tests for same authenticated account+peer positive evidence; different or
   envelope-only account; inactive/retracted fact; inferred/newer-than-evidence/
   expired/revoked alias rejection; inbound-only and outbound-only cases; no-peer
   null; alias-only and literal-plus-active-alias null absent true; newly minted,
   incomplete, gapped, member-changed, or partial-window account-universe null;
   full complete-universe/full-account-coverage fresh false; recent-but-partial
   coverage null; reset/gap/re-auth null; transient receipt-delay/future-skew
   rejection; duplicate-ingress immutable receipt time; invalid-age/reset/gap/
   re-auth/rebind/principal-mismatch closure; 180-day boundaries for every private
   record; and zero materialization of raw values.

### Phase 6 - maintenance, documentation, and rollout

1. Add the deterministic maintenance handler in
   `src/butlers/scheduled_jobs.py`, schedule it in
   `roster/messenger/butler.toml`, and make its only effects deadline,
   dispatch/lease/report-fence, coverage-closure, complete-universe continuity
   rollover, and retention maintenance.
2. Update `roster/messenger/MANIFESTO.md`, `roster/messenger/AGENTS.md`,
   `docs/modules/email.md`, `docs/connectors/gmail.md`, and topology docs to
   distinguish SMTP acceptance from provider-Sent confirmation and document the
   privacy boundary, entity-wide alias-negative-null rule, and complete-universe
   negative-evidence constraint.
3. Ship feature flags disabled.  Only after all owner gates and exact-head test
   evidence may an approved single provider/account canary begin.  Observe only
   unlinkable categorical count/timestamp/freshness/coverage telemetry.  No
   backfill or mailbox scan is an activation step; `false` remains unavailable
   until natural post-enable coverage spans 180 days.

## Required verification matrix

| Layer | Required proof |
|---|---|
| Storage | Migrated PostgreSQL schema has only allowlisted columns, state/check/index invariants, unique opaque ingress-deduplication token, complete-universe continuity bounds, separate 180-day anchors for dispatch/report and confirmation fences, and no JSONB/free-text escape hatch. |
| Transactionality | Intent commit occurs before provider call; failed commit sends nothing; concurrent/retry paths cannot duplicate unknown external work; native send dispatch/report is atomically idempotent before confirmation leasing. |
| Provider | Only Messenger-native direct send references reported through the fenced broker callback may confirm; generic SMTP and all unproven cases remain unknown/unleased; no scan/list/search/backfill or reconciliation mutation exists beyond the approved native send. |
| Broker and ingress | Credential scope derives account principal; forged/revoked/cross-account caller, direct Messenger access, dispatch/send-report/lease/confirmation-report replay, envelope-only identity, invalid transient age assertion, duplicate ingress, pre-enforcement history, gap/reset/re-auth all fail closed.  The exact hardened `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)` ACL/signature is exercised atomically for invalid-age/checkpoint-gap/re-auth/rebind/principal-mismatch and cannot leave a stale fresh watermark. |
| Privacy | Candidate-path DB/log/metric/aggregate/result outputs, including pre-admission failures, have no content, header, attachment, raw provider payload, audit text, secret, recipient echo, raw error, or full envelope. |
| ACL and topology | Relationship gets execute only after both schemas activate, cannot direct-select/list; connector/dashboard/public cannot invoke the aggregate or private tables; migration order stays disabled/unknown until ready. |
| Correlation | Exact authenticated canonical account + exact active peer inside 180d is positive; alias guess/newer-than-evidence/expired/revoked alias, provider account alias, different or envelope-only account/inbound-only is not. |
| Freshness | `false` requires literal active peers only, no active alias authority for the entity, one complete-universe continuity interval and both account epochs spanning 180d for every member account, with no qualifying leg for every literal peer; all alias-derived, incomplete, partial, stale, or unknown cases force null; UI/API copy uses the same tri-state. |
| Rollback | Capability disable returns unknown; downgrade stops on non-empty data; no destructive data action occurs silently. |

Run, from the exact implementation head, at minimum the targeted tests above,
`openspec validate true-bidirectional-email-correspondence --strict`,
`git diff --check`, touched-code lint/type checks, scoped documentation/guard
checks, terminal hosted CI, and an independent exact-head privacy/security
review.  A full repository suite is a merge-readiness signal, not a substitute
for the provider/migrated-DB/privacy proofs.

## Rollback procedure

1. Disable the provider/account capability.  Stop issuing leases and return
   `freshness=unknown`, `bidirectional=null` to Relationship.
2. Revoke aggregate execute if the security owner directs complete isolation;
   do not replace it with a raw table read.
3. Keep private rows through their scheduled 180-day expiry.  Do not rewrite,
   backfill, or silently delete them.
4. If a schema downgrade is necessary, run its empty-only guard first.  A
   non-empty ledger is an owner decision, not a permission to destroy data.

## Remaining owner gates

- Provider owner: Messenger-native exact-reference send, Gmail Sent proof,
  allowed OAuth scope, capability SLA, canonical account, fenced dispatch/send-
  report/confirmation-report idempotency, transient provider-age source/budget,
  full-window coverage rules, and no-scan implementation review.
- Identity/privacy owner: active-literal `has-email` v1 decision or alias
  authority source/creator/version/revocation/expiry rules, evidence-time/query-
  time authority semantics, entity-wide alias-negative-null behavior,
  exact-account-only scope, separate 180-day anchors, data deletion posture, and
  content-free observability.
- Product/operations owner: complete canonical account-universe configuration
  source and completeness, disabled/unproven account inclusion, identical-set
  continuity rollover, and the 180-day negative-evidence hold after any
  member-set change, gap, or expiry.
- Security owner: authenticated ingress/broker transport, pre-route candidate
  error scrub, atomic fixed ingress projection and coverage-close functions with
  their minimal schema-usage/exact-execute grants, cross-chain aggregate
  installer/function owner/search path/grants, and independent migrated-ACL test
  review.
- Messenger/Switchboard owner: native-send broker boundary, pre-admission
  privacy-candidate behavior versus existing generic notification/audit history,
  and backwards compatibility of categorical outcomes.
- Relationship owner: separate known-entity job, aggregate receipt-time/coverage
  semantics, and no-fact/no-claim treatment for partial, stale, or unknown
  coverage.
- Operations/product owner: disabled-by-default rollout, canary, freshness
  language, alerts, rollback, deployment, and live-data authorization.
