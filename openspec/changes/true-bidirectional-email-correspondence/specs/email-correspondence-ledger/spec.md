## ADDED Requirements

### Requirement: Messenger-private metadata-only correspondence ledger

Messenger SHALL own a private `messenger.email_correspondence` ledger for
new outbound email attempts.  A row SHALL contain only a server-derived opaque
intent/idempotency identifier, canonical provider account reference, normalized
bare peer address, optional provider message/thread references, one categorical
state (`accepted`, `confirmed`, `failed`, or `unknown`), and lifecycle
timestamps.  The schema SHALL contain no JSONB or free-text payload field.

The ledger and every new correspondence-specific audit, trace, log, metric,
lease, cache, and aggregate SHALL NOT persist a subject, body, header,
attachment, raw provider payload, provider response text, audit text, secret,
display name, message-content hash, or raw error string.  Existing
content-bearing generic audit, notification, inbox, and ingestion stores SHALL
NOT be read as correspondence evidence or copied into this ledger.

Any qualified inbound observation, coverage epoch, account-universe membership,
native-send dispatch/report fence, confirmation lease/report fence, or explicit
alias-authority record SHALL likewise use an explicit metadata-only allowlist with
no JSONB/free-text escape hatch.  A qualified inbound observation MAY retain one
non-reversible, account-scoped opaque source-deduplication token, but no raw source
event ID.  Each record SHALL retain only the opaque IDs, canonical account/peer
values where strictly required, categorical state, and timestamps described by
this change; it is never a raw-event mirror or a general address book.

#### Scenario: A new intent records only allowlisted metadata

- **WHEN** Messenger admits a newly approved email send or reply
- **THEN** it creates or locks exactly one private ledger row using the
  server-derived opaque idempotency identifier
- **AND** the row contains only the allowed account, normalized peer, optional
  provider references, categorical state, and timestamps
- **AND** it contains no content, provider payload, audit text, secret, or
  free-text error field

#### Scenario: Content-bearing stores cannot become evidence

- **WHEN** the correspondence service evaluates a send, confirmation, or
  Relationship aggregate
- **THEN** it does not read `public.audit_log`, `switchboard.notifications`,
  `switchboard.message_inbox`, or the retired Messenger delivery tables as
  correspondence evidence
- **AND** it does not copy a `notify.v1` envelope or a generic audit payload
  into any correspondence-specific persistence surface

#### Scenario: Ledger rows are not visible through a generic observability path

- **WHEN** an operator views logs, metrics, or a Relationship result for the
  new correspondence feature
- **THEN** the surface contains only bounded categorical counts, timestamps, or
  freshness states allowed by this specification
- **AND** it does not expose a peer address, account reference, provider
  message/thread reference, raw row, or correspondence content

### Requirement: Transactional intent and conservative state transitions

Messenger SHALL persist the correspondence intent before initiating external
email egress.  A unique opaque idempotency key and row lock SHALL serialize
concurrent attempts.  The ledger state machine SHALL be conservative:
`accepted` records known transport/provider acceptance, `confirmed` records
provider-Sent proof, `failed` records a known pre-acceptance rejection, and
`unknown` records a not-yet-dispatched intent or any indeterminate outcome.
Only `confirmed` is eligible as outbound evidence.  An accepted record whose
confirmation deadline expires SHALL become `unknown`; no state implies that an
email was received or read.

#### Scenario: Intent persistence failure prevents egress

- **WHEN** the private intent insert or idempotency lock cannot commit
- **THEN** Messenger returns a classified internal failure before invoking the
  email provider
- **AND** it does not send an unrecorded email attempt

#### Scenario: SMTP acceptance is not correspondence proof

- **WHEN** a generic SMTP transport accepts a message
- **THEN** Messenger records `accepted` with its acceptance timestamp
- **AND** the result is not eligible for a bidirectional aggregate
- **AND** when no capable provider confirms it by the deadline the row becomes
  `unknown`

#### Scenario: Crash after external dispatch is indeterminate

- **WHEN** a worker crashes or times out after beginning an external send and
  before recording a known provider outcome
- **THEN** recovery leaves or changes the row to `unknown`
- **AND** it does not blindly resend the email on process restart
- **AND** it schedules only exact-reference reconciliation when a capable
  provider reference already exists

#### Scenario: Provider idempotency governs a retry

- **WHEN** a provider documents an idempotency mechanism bound to the stored
  opaque key and a retry is otherwise authorized
- **THEN** Messenger reuses that exact key and preserves one ledger intent
- **AND** when the provider lacks that guarantee an `unknown` attempt is not
  automatically resent

### Requirement: Exact-reference provider-Sent confirmation

An outbound record SHALL transition to `confirmed` only when an enabled provider
capability proves that the exact message reference for the same canonical account
is present in that provider's Sent representation through its documented exact-ID
metadata operation.  The provider handoff SHALL pass only account reference,
message/thread reference, categorical result, and observed timestamps.  It SHALL
never list/search/traverse a Sent mailbox,
perform historical backfill, use the connector's broad Sent-ID cache, fetch
content, or mutate provider data during confirmation.  The separately approved
provider-native send is the sole egress exception.  Gmail SHALL obtain and
report the handoff only through an authenticated Switchboard broker.  Messenger
SHALL first issue a one-time native-send dispatch bound to the broker-derived
connector/account principal, opaque dispatch ID, fence, and deterministic
send-report ID.  It SHALL atomically validate and consume the matching
`correspondence.send.report` before it records a direct native `accepted` result
with its exact reference, a known `failed` result, or an indeterminate `unknown`
result.  An identical retried send report returns its prior categorical result;
a conflicting, stale, expired, cross-account, or altered-fence report fails
closed.  Only the recorded accepted exact reference may receive a confirmation
lease.  Messenger SHALL then bind every confirmation lease and result to the
broker-derived connector/account principal, one-time opaque lease/fence/
deterministic report ID, and exact known provider reference.  It SHALL atomically
consume a confirmation lease and record its outcome under that fence; an
identical retried confirmation report returns its prior categorical result while a
conflicting, stale, or expired report fails closed.  It SHALL not accept a direct
connector call, generic ledger writer, or caller-supplied account/provider
identity claim.

#### Scenario: Exact provider confirmation is recorded

- **WHEN** a capable provider returns a stable exact message reference and its
  documented exact-ID metadata lookup proves it is in Sent
- **THEN** Messenger verifies the account/reference match against its private
  intent and records `confirmed` with the observed timestamp
- **AND** no raw provider response or mailbox content is persisted

#### Scenario: A native send result is recorded before confirmation leasing

- **WHEN** an authenticated Gmail connector completes one Messenger-issued
  native-send dispatch
- **THEN** it reports only the dispatch ID/fence/deterministic send-report ID,
  categorical outcome, direct exact reference when present, and allowed timestamp
  through `correspondence.send.report`
- **AND** Messenger atomically validates and consumes that dispatch report before
  recording `accepted`, `failed`, or `unknown`
- **AND** only an accepted result with the recorded direct exact reference may
  later be leased for provider-Sent confirmation
- **AND** an identical report is idempotent while a conflicting or stale report
  fails closed without another provider action or state transition

#### Scenario: A provider lacking Sent proof remains unknown

- **WHEN** a provider accepts an outbound message but cannot prove the exact
  message is in Sent under its approved capability contract
- **THEN** Messenger never records `confirmed` for that attempt
- **AND** after its confirmation deadline the state is `unknown`

#### Scenario: Reconciliation cannot enumerate mail

- **WHEN** the Gmail connector leases a pending confirmation through Switchboard
- **THEN** the lease is bounded to an already-known provider account and exact
  message reference
- **AND** the connector performs no `list`, search, history traversal, Sent
  scan, or backfill to satisfy the lease

#### Scenario: A forged connector/account cannot obtain or confirm a lease

- **WHEN** a connector call has no valid scoped transport credential, names a
  different provider/account than its authenticated principal, reuses an
  expired/consumed lease, or changes its leased provider reference
- **THEN** Switchboard rejects it before routing to Messenger
- **AND** Messenger records no confirmation or state transition
- **AND** the response and logs reveal no account, reference, credential, or
  raw ledger detail

#### Scenario: An identical broker report is crash-safe and idempotent

- **WHEN** Switchboard retries the same authenticated report after an uncertain
  route response
- **THEN** Messenger uses the broker-derived principal, lease ID/fence, and
  deterministic report ID to return the already-recorded categorical result
- **AND** it does not issue a second provider action or state transition
- **AND** a mismatched outcome or fence fails closed without consuming a new
  lease

### Requirement: Bounded evidence, identity authority, retention, and freshness

Bidirectional email evidence SHALL use a rolling 180-day window.  A qualifying
inbound observation SHALL be created only from an email event accepted under an
authenticated Switchboard connector-ingress epoch.  Switchboard SHALL derive the
connector/provider/endpoint principal from transport authentication, verify its
canonical account binding, and reject caller-supplied envelope identity as proof.
The private observation contains only the canonical account, normalized bare
peer, opaque epoch, and server `received_at`; it SHALL not copy payload/raw
envelope provider time, headers, event IDs, threads, or content.  Events before
enforcement, after an epoch reset/gap/re-authentication/account-rebinding, or
with a principal/account mismatch SHALL not qualify.

At the authenticated ingress seam, the credential-bound provider adapter MAY
derive one strictly typed, transient provider-age assertion from its documented
provider event-time field.  Switchboard SHALL validate that assertion only
against the broker-derived principal, server clock, and owner-approved maximum
age/future-skew budget, then discard it.  It SHALL not accept a generic caller or
raw-envelope time field, persist the assertion in `public.ingestion_events` or a
private correspondence record, expose it to the aggregate, or use it as the
180-day evidence time.  Missing, malformed, future-skewed, or over-age assertions
SHALL create no qualified observation and SHALL atomically invoke only
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
with the fixed `age_invalid` category to close or block the inbound epoch before
the generic event path returns.

The authenticated ingress path SHALL derive a non-reversible, account-scoped
opaque source-deduplication token from the authenticated provider's immutable
source-event key using a secret held outside the database.  It SHALL atomically
write at most one qualified observation and any related coverage advancement with
the accepted ingress decision through a Messenger-owned non-generic projection
writer.  The raw source event ID SHALL not be retained.  A duplicate/retry SHALL
return the original categorical result and SHALL NOT insert a new observation,
replace the original server `received_at`, or refresh coverage.

The only other qualified-ingress coverage mutation SHALL be the fixed
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
function.  Its inputs are only broker-derived provider, canonical account,
authenticated ingress epoch, bounded closure category
(`age_invalid`, `checkpoint_gap`, `reauth`, `rebind`, or `principal_mismatch`),
and once-captured server closure time; it returns only `closed` or
`already_closed`.  It SHALL be idempotent, accept no peer/raw source ID/provider
time/content/free text/caller principal, and execute in the same authenticated
ingress transaction that detects the condition.  Reset, gap, re-authentication,
account rebinding, or principal/account mismatch SHALL invoke the corresponding
category before any event can qualify, so a prior watermark cannot remain fresh.

The peer SHALL resolve either by an active literal
`relationship.entity_facts` `has-email` value after the shared bare-address
normalizer or by an explicit provider peer-alias authority for that exact
provider/canonical-account/entity/peer tuple.  An authority SHALL have an
approved fixed source/writer, version, issued/expires/revoked state, separate
180-day lifecycle, and no auto-renewal by correspondence.  It SHALL be active at
query time and must cover every qualifying evidence timestamp under the half-open
interval `[issued_at, expires_at)`: `issued_at <= evidence_at < expires_at` and
no revocation at or before `evidence_at`.  A newly issued authority SHALL NOT
retroactively match older evidence.  Same-account matching SHALL require exact
equality of the transport-authenticated canonical provider account and ledger
account; provider account aliases are out of scope.
The system SHALL never infer an alias from a display name, domain, plus-addressing,
thread context, stale fact, or provider guess.

For each canonical account, metadata-only authenticated-inbound and
provider-native-outbound coverage epochs SHALL record contiguous
`covered_from`/`covered_through` bounds.  Inbound advances only after a successful
committed authenticated checkpoint.  Outbound begins only after an approved
enforcement checkpoint establishes that every eligible Messenger send for the
account is admitted to the private ledger/native path, then advances from
committed service-health checkpoints even if no email is sent.  They start no
earlier than enforcement or capability enablement, close on reset/gap/re-auth/
rebinding/native-path fallback-or-disable/failed proof/retention discontinuity,
and never backfill prior time.  The complete account universe SHALL be a separate
private, owner-approved static inventory of every canonical provider/account
through which the owner can possibly send or receive email, including disabled,
unsupported, and unproven accounts.  It SHALL never be inferred from connector
traffic, mailbox data, provider listings, historical envelopes, or backfill.  A
membership has only account/provider, opaque universe epoch, rolling
`covered_from`/`covered_through`, last complete-configuration checkpoint,
categorical continuity state, and expiry/closure timestamps; it exposes none of
those values to Relationship.  It may roll to a successor only where the member
set is identical and the preceding complete interval ended without a gap.  An
addition, removal, rebinding, expiry, incomplete inventory, or continuity failure
closes the interval and a later one starts anew.

`bidirectional=false` SHALL be returned only when the entity has at least one
active literal `has-email` peer, has no active explicit peer-alias authority, and
a single current complete-universe continuity interval spans the requested full
180-day window.  The aggregate is entity-wide rather than peer-scoped: absent a
positive result, any active alias authority forces `null`, even if the entity also
has literal peers.  Alias-derived negatives are intentionally unavailable in v1:
the 180-day half-open alias authority lifetime cannot both span the rolling window
and remain active at query time.  For every literal peer, every canonical account
member of the complete universe SHALL have both relevant epochs continuously
cover the full requested 180-day window and no qualifying leg.  The aggregate
SHALL NOT choose one covered account from an ambiguous universe.  A recent fresh
checkpoint alone is insufficient.  No active literal peer, any active alias
authority, an absent/incomplete/expired/gapped/non-spanning universe, or a lack
of fresh or complete coverage SHALL produce `freshness='stale'` or `'unknown'` and
an indeterminate (`null`) result, not false absence.

Correspondence rows, qualified inbound observations (including opaque source-
deduplication tokens), coverage epochs, account-universe memberships, native-send
dispatch/report fences, confirmation leases/report fences, provider peer-alias
authority metadata, and aggregate caches SHALL be hard-deleted no later than 180
days after their own intent, receipt, epoch, universe-membership issuance,
dispatch/lease/report, or authority-issuance anchor.  Aggregate counters may
outlive those records only when they have no account, peer, intent, dispatch/lease,
provider-reference, or other linkable identifier.

An active coverage or complete-universe epoch SHALL roll into a fresh bounded
interval before its 180-day retention deadline.  The successor SHALL keep only
current rolling coverage or membership bounds and categorical continuity, not a
historical event list, historical membership list, or pre-window evidence, and
SHALL NOT bridge a reset/gap, member-set change, or create backfill.

#### Scenario: Same-account and active-peer evidence produces a positive result

- **WHEN** a `confirmed` outbound record and a qualifying inbound email belong
  to the same canonical account and exactly resolved peer inside 180 days
- **AND** both evidence sources have current authenticated coverage
- **THEN** the bounded aggregate may report bidirectional correspondence as
  `true`

#### Scenario: Inbound recurrence alone is insufficient

- **WHEN** Relationship sees repeated inbound messages for a peer but no
  qualifying `confirmed` outbound record exists
- **THEN** the aggregate does not report bidirectional correspondence as `true`
- **AND** the prior inbound-only heuristic is not relabeled as provider proof

#### Scenario: Alias inference is rejected

- **WHEN** an inbound sender differs from an active `has-email` value and has
  no explicit current, evidence-time-valid provider peer-alias authority for the
  target entity
- **THEN** the correspondence aggregate treats it as unmatched
- **AND** it does not infer equivalence from address spelling, display name,
  domain, thread, or a retracted/superseded fact

#### Scenario: A newly issued alias cannot rewrite older evidence

- **WHEN** a peer-alias authority is issued after an otherwise matching inbound
  or outbound evidence timestamp
- **THEN** that authority does not qualify the older evidence even if it is
  active at query time
- **AND** an authority for which `evidence_at >= expires_at`, or which was
  revoked at or before evidence time, or is no longer active at query time,
  likewise does not qualify it

#### Scenario: Alias authority cannot create entity-wide negative proof

- **WHEN** an entity has no active literal `has-email` peer, has only an alias
  peer, or has any active peer-alias authority alongside its literal peer
- **THEN** the aggregate reports `freshness='unknown'` and `bidirectional=null`
- **AND** full account coverage alone does not return `false` for that entity

#### Scenario: Expired evidence cannot influence an aggregate

- **WHEN** a correspondence record reaches 180 days after its intent timestamp
- **THEN** bounded maintenance removes the record and associated private
  correspondence metadata
- **AND** subsequent aggregate results do not include it

#### Scenario: Stale coverage never becomes negative proof

- **WHEN** the provider confirmation or inbound account watermark is stale or
  unavailable for a requested entity
- **THEN** the aggregate reports `freshness='stale'` or `freshness='unknown'`
- **AND** its bidirectional field is `null` rather than `false`

#### Scenario: Fresh partial coverage never becomes negative proof

- **WHEN** a post-enable inbound or provider epoch has a current checkpoint but
  does not continuously cover the entire 180-day window
- **THEN** the aggregate reports `freshness='unknown'` or `freshness='stale'`
- **AND** its bidirectional field is `null`, not `false`

#### Scenario: A current universe cannot rewrite the historical account set

- **WHEN** a complete account-universe membership is newly minted, incomplete,
  expired, gapped, member-changed, or its continuity interval does not span the
  requested 180-day window
- **THEN** the aggregate returns `freshness='unknown'` or `'stale'` and
  `bidirectional=null`
- **AND** it does not choose one covered account or omit a former/unproven member
  to return `false`
- **AND** only an identical-member-set successor with unbroken continuity can
  preserve the universe interval for a later negative evaluation

#### Scenario: Delayed or replayed ingress cannot refresh evidence time

- **WHEN** an authenticated provider-age assertion is absent, malformed, outside
  the maximum delay, or beyond future-skew budget
- **THEN** Switchboard creates no qualified observation, retains no provider time,
  and atomically invokes
  `messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
  with `age_invalid`
- **AND** no prior inbound coverage watermark remains usable as fresh
- **AND** the aggregate cannot treat the event as fresh receipt-time evidence
- **WHEN** the same accepted source event is retried with the same opaque
  account-scoped deduplication token
- **THEN** the projection returns the existing categorical result without
  replacing `received_at` or advancing coverage

#### Scenario: Historical envelope identity cannot qualify

- **WHEN** an inbound event predates authenticated ingress enforcement or its
  provider/account identity was supplied only by the envelope
- **THEN** no qualified inbound observation is created from it
- **AND** it cannot contribute to same-account correspondence evidence
