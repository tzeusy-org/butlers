# Capability broker transport foundation

## Declared approval modes and broker catalog projection

The checked-in central inventory declares `approval_policy.mode=default`.
Conditional module registrations declare their mode through the same existing
`ToolMeta.approval_policy` mapping; `calendar_respond` explicitly declares
`always_per_event`. The daemon preserves that source declaration while adding
its actual `enabled`, `configured_gate`, expiry, risk and rule-precedence facts.
Configuration cannot replace the declaration. Modules declare only the mode,
not the daemon's configuration-derived authority fields. Legacy metadata with
no declared mode retains its ordinary callable behavior but is not eligible
for broker projection.

The authorization catalog check rejects absent or unknown modes and rejects
`always_per_event` without both an enabled approvals module and a configured
gate for the actual tool. Calendar invitation answers cannot declare default.
The mode is already part of the descriptor's frozen policy mapping and catalog
generation digest; no new top-level publication field is introduced.

`capability_broker.catalog.project_catalog()` converts a verified final
registration to broker capability values. It preserves exact resource-selection
marks, recipient declarations, final post-approval input schemas and gate facts.
It detaches mutable schema values and omits private definitions. An enabled
owning approvals module supplies the descriptive availability fact for
broker-induced PARK, including a default-mode tool with no configured gate.
A configured enabled gate supplies `approval_required`; explicit
`always_per_event` additionally prevents standing-rule automatic approval.

`PublishedAuthorities`, an explicit `Broker` constructor input, reads the
delivered M1 `PublishedCatalogProvider` on the broker's existing goal-locked
connection. Each listing and call verifies the complete enabled current fleet,
its runtime role, live leases, membership, registration bodies and digests, then
projects every final definition. The broker compares the resulting fleet
definition digest with the locked goal's admission digest. A local per-domain
generation digest is not substituted for that fleet pin. There is no cache,
pool acquisition or network request under this reader. Retirement, incomplete
publication or changed definitions refuse rather than reusing an older view.

This adapter supports reads and records whose complete metadata declares no
recipient or inherited-party condition. Health's real `measurement_log`
registration is a recipient-free, non-owner-authority record. It reaches the
existing current-Grant, exposure, budget, duplicate and Stop checks; exposed
owner-authority records still refuse. Ordinary handler acknowledgement remains
UNKNOWN without an authoritative owning effect receipt.

A durable `park_only` Grant still requires enabled owning approvals. Health's
checked-in approvals section currently omits `enabled=true`, so its mandatory
PARK path refuses before charging. Positive software and authored PostgreSQL
controls use an explicit enabled test configuration through the actual finalizer,
not a deployment change or a synthesized descriptor. The purpose-bound owning
consumer stores the exact call, waits for the owner decision and executes the
stored original or edited arguments at most once after committed consumption.
Ambiguous acknowledgement never grants another execution. Calendar's separate
canonical prepared-command integration remains unfinished; no raw Calendar PARK
or execution success is implied.

The adapter supplies no standing rule, owner default or
actuation risk. Calls requiring those policy facts and all selection-bound
resource checks refuse until their actual owning producers are integrated.
Listing an otherwise granted definition does not promise its policy-dependent
call is currently executable. Selection and resource values remain distinct:
published resource parameter names alone grant neither selection nor values.
Owning approval storage/receipt/replay and authenticated ingress remain required.
Mode metadata and policy tests do not implement owning always-per-event
rule-creation, matching or promotion enforcement, enable private transport or
activate the runtime.

The daemon's dedicated private transport is default OFF. It is separate from the
ordinary model-presentable MCP catalog. The central cancellation and replay
consumers below remain distinct from the missing owning approval producer,
authenticated Stop ingress, runner and positive owning replay executor.

`[butler.capability_broker_transport]` is a strict typed section mapped by
`load_config()` into `ButlerConfig.capability_broker_transport`. `enabled` accepts
only a TOML boolean and defaults to false. Unknown keys and malformed values
refuse even when OFF. The literal-disabled subtree is detached only while the
ordinary global ENV resolver runs, then restored unchanged for strict validation.
Its unused endpoint and dedicated material path placeholders are not resolved or
opened. Unrelated missing ENV variables continue to refuse normally.

When enabled, the closed shape requires `role`, `domain`, `peer` and `material`.
`role` is `broker-control` or `owning-service`. A control client additionally
requires an explicit HTTPS endpoint with port and exact `/mcp` path, an
`owning-service` peer and no listener fields. An owning listener requires
`listen_host`, `listen_port`, a `broker-control` peer with
`client_certificate=true`, and no client endpoint. The peer has an exact domain
and explicit non-wildcard `sans` list. Material names are `ca_path`,
`certificate_path`, and `private_key_path`; parsing these names performs no
material I/O. Neither these settings nor a callback, header, caller flag, or
KEY_ABSENT_STAGE grants production authority.

Enabled owning startup requires the matching daemon domain, active approvals
module, current/next verification keys, final registered catalog/originals and
actual019 role/schema/guard verification before material access. Missing inputs
refuse as `private_transport_prerequisites_unavailable`; successful parsing is
not deployment or SQL proof. Central clients are assembled only by the separate
trusted broker profile. There is no public-MCP fallback or downgrade to OFF.

The private boundary occurs after module runtime-state initialization and before
approval-delivery/public MCP startup. `ButlerDaemon.start()` rolls back only its
private resource owner if a subsequent startup step fails or is cancelled.
Shutdown closes that owner after disabling acceptance and before its first
ordinary awaited shutdown step. Cleanup failure stays visible and the unsettled
handle is retained. Repeated close joins the same retained task and absolute
budget rather than retrying or refreshing grace.

The internal resource owner uses the existing `build_client`, `build_server`,
and `PrivateServer.run()` APIs. One retained lifetime task enters and exits the
actual server context in the same task. Client and server cleanups begin together
under one five-second owner bound; their native A1 limits remain unchanged.
Timeout means incomplete cleanup, not successful FD settlement. A failed partial
acquisition retains an unsettled owner on the original exception. Software tests
use finite ephemeral loopback TLS and explicit synthetic operations; they do not
establish durable A2 receipts, production authority, database role/race outcomes,
or enabled deployment.

The complete RFC0038 broker acceptance criteria, positive UnitB/replay producer,
A2 storage and receipt projection, A4 worker, genuine PostgreSQL tests, exact-head
hosted/protected evidence, provisioning and activation remain required. This
foundation alone is not feature completion.


## Session endpoint and invocation credentials

`BrokerSessionListener` owns a separate broker-only socket using the existing
control listener's finite startup/shutdown lifecycle and the real FastMCP ASGI
lifespan. It mounts only `/goals/<goal-id>/mcp/`; every HTTP request authenticates
before MCP processing. Discovery and calls re-read the invocation and current
catalog in their own goal-locked transactions. The existing `KEY_ABSENT_STAGE`
command still creates only `SessionRefusalListener`. The separate explicit
`TRUSTED_BROKER` profile assembles the central runtime described below; it creates
no ambient broker, public token route, runner invocation or deployment activation.

The broker's private `issue_invocation_token` hook issues 256 random bits for an
already committed, runner-owned **launching** invocation. It checks the actual
Switchboard role, locks goal then invocation, requires a running goal, current
lead epoch (children remain independent), valid lease and canonical stored grant
within the goal ceiling. A conditional update stores only the token hash.
Plaintext is returned only after confirmed commit. Repeated issuance while that
hash exists refuses; an uncertain commit returns no credential and is not retried.
The runner must finish or recover that invocation through its own lifecycle.

`revoke_invocation_token` clears the hash only after the runner records one of
its existing terminal statuses. It refuses a still-live invocation rather than
making it eligible for a replacement token. Stop already clears credentials
under the same goal-first order. These private methods do not create invocation
rows, authenticate a runner request, write sandbox MCP configurations, terminate
processes, supply D8 tools, or perform invocation budget accounting. Those actual
runner/session-to-goal producers remain required. Missing catalog publication,
selection/recipient/rule authority and owning execution adapters likewise remain
fail-closed dependencies; injecting a test implementation establishes none of
that production authority.


## Bounded Stop cancellation consumer

`GoalStop.cancel_candidates()` reads at most four stale-able pending or expired
claim identities under the existing role/schema checks and five-second local
transaction bound. Discovery mutates/locks no work row; the later goal-first
claim revalidates the actual stopped goal, dispatch witness, effect and tuple.
The constructed `CentralRuntime` passes only its fixed configured client domains
to discovery before `LIMIT`, so older unconfigured work cannot starve a configured
owner. Existing callers can omit the filter; neither form grants claim authority.

`CancellationWorker.run_batch()` is an explicitly invoked, sequential finite
consumer. It constructs no transport or scheduler. An enabled `CentralRuntime`
with configured approval clients owns one cancellation task, using its existing
central pool and fixed purpose-bound `PrivateClient` instances. Each completed
batch is followed by a one-second idle interval; OFF and absent-client startup
create no cancellation task. First-generation claims may CANCEL once; reclaimed generations
first LOOKUP the original tuple. A fresh pending/approved or unknown/null LOOKUP
may ask the owning A2 producer to atomically revalidate and CANCEL once. Unknown
is never an absence or completion proof. Dispatched/committed/refused LOOKUP,
malformed RPC/receipt, transport failure and uncertain ACK cause no further
request in that item. Only a matching cancelled/fenced-absent receipt reaches
`ack_cancel_work` with the original local claim; A0 rechecks the current DB lease,
generation, stopped goal, tuple and no-dispatch witness before committing ACK.

The consumer admits whole native phases: existing five-second SQL operations,
existing ten-second private calls including their cleanup, five seconds for ACK
and one second bookkeeping reserve. Item admission is bounded by 30 seconds;
batch admission by 125 seconds. The worst-case reclaimed path need not make
progress in one item: if the complete next phase plus ACK reserve does not fit,
it remains unknown rather than clipping a transport timeout or refreshing grace.
Client idle cleanup remains the resource owner's separate five-second phase,
after both runtime consumers are terminal. Runtime shutdown cancels and joins
approved replay and cancellation before closing their private clients, while
attempting listener release independently. The existing absolute six-second
runtime cleanup bound is not extended by either worker: incomplete settlement
raises a timeout and retains unfinished tasks; it never closes a client under
a live private call or reports Stop globally complete. Repeated caller
cancellation does not renew that deadline. No claim lease renewal, manual
transport retry, new daemon/profile or global Stop-completed response is added.

Real TLS software controls exercise this bridge using explicitly synthetic
owning receipts and a repository control. They establish neither A2 durable
commit authority nor SQL claim/ACK/race correctness. The actual A2 gate/tombstone
producer, authenticated approved replay/executor handoff, isolated production
profile and resource-owner attachment, genuine role/PG/transport races, runner
termination and terminal hosted/protected evidence remain mandatory before
production enablement or full broker acceptance.


## Owning rejection and expiry observations

The existing runtime replay worker first uses fixed-peer private `LOOKUP` for
configured owning domains. Unconfigured hints cannot fill its discovery batch.
An actual rejected or expired action produces a stable receipt for its current
owning gate version, under the original goal/action lock and the same finite
transaction. The receipt guard requires the matching current action status;
lookup returns only after commit. Repeated lookup reads the same receipt, and a
lost response gives no permission to execute. Abandoned actions remain
unclassified rather than being relabeled as rejection, expiry or cancellation.

The central consumer re-locks the goal and full original PARK association before
persisting `rejected` or `expired`. A stopped goal, existing dispatch intent,
changed tuple or conflicting terminal outcome refuses. Identical terminal
readback is idempotent. No connection stays held across the private request;
there is no new endpoint, issuer, handler call or execution authorization.
Approved observations still enter the existing signed snapshot, current-grant
and one-winner replay protocol. Unknown/refused evidence leaves the effect
unsettled. Reclaimed Stop work that observes a negative decision still requires
the original-tuple `CANCEL` fence and its actual ACK, preserving owner history.

Software controls exercise constructed runtime/registration and the real owning
decision writer with explicit SQL and private-exchange collaborators. Migrated
runtime-role commit/readback, rollback, lost-response and Stop race controls are
authored and collected only locally; those software results do not prove SQL
durability, live owner authentication, provider effects or full broker delivery.

## Central stored-approved dispatch transaction

The private `prepare_approved_dispatch` consumer implements the central half of
approved replay against existing sw047/sw048. It is absent from both MCP
listeners. `StoredGoalCall` is data, not proof that an owner approved anything;
the fixed replay orchestration obtains an authenticated owning snapshot,
revalidates locally and requires a committed witness before one execution.
Without the configured owning transport that orchestration remains unavailable.

The consumer takes the same verified runtime/RLS transaction and goal-first lock
as Stop. It checks the original invocation, immutable PARK tuple and original
digest, exact approval reference, current grant/ceiling/catalog, resources,
selection, exposure and independent policy refusals. The owner's edited digest
is distinct from the immutable effect digest. It commits one dispatch intent
before returning; it neither creates another effect nor charges the original
PARK again. A returned invocation needs no resurrected bearer token: D13 permits
stored approvals during `settling`. Stopped/terminal goals, cancellation work,
any earlier dispatch intent, mismatches and commit uncertainty refuse. A lost
PARK response may attach only the exact action/effect reference.

`settle_approved_dispatch` binds that intent and original effect identity in a
fresh goal-first transaction. Earlier dispatched work may settle after Stop;
Stop does not claim it undone. Untrusted output exposure commits with settlement
before any value returns. A failed/unknown settlement never permits redispatch.
The central prepare/settle primitives perform no domain network request.
The fixed replay orchestration joins them to the owning019 producer described
below. Genuine current PostgreSQL outcomes, positive UnitB and complete original
broker delivery remain separate acceptance obligations.

## Private PARK and same-transaction recipient readers

The broker's explicit `approvals=PrivateApprovals(...)` consumer sends the
already committed original PARK intent to a fixed, purpose-bound owning-service
`PrivateClient`. It snapshots the arguments before awaiting, binds the action ID
to the effect ID, carries the signed D7 assertion and standing-rule restriction,
and accepts only the matching original-tuple `parked` receipt/reference. Missing
peers, unknown/refused/malformed responses and transport failures settle UNKNOWN;
a configured consumer never falls back to the ordinary domain adapter or retries.
Stop during the forward can retain the late matching reference while keeping the
central effect unknown and its cancellation work outstanding. Existing callers
that supply only `Domains` retain their established interface. The client owner
still supplies acquisition and finite shutdown; this consumer opens no listener,
reads no material and enables no profile.

`RecipientAuthorities` wraps the real catalog/selection/policy authority without
replacing it. On the broker's existing transaction/connection it reads at most two
live owner entities and locks the selected rows, resolves explicit entity/channel
selectors through the actual identity rules, and binds the result to the exact
capability and raw-argument digest. Unknown, conflicting, ambiguous, inherited or
unsupported parties remain non-owner. Only the verified native core-notify
resolver may distinguish its declared implicit Telegram owner branch from its
conditional unresolved reply/context branch. Its internal resolver tag must come
from the verified final core.notifications descriptor; neither a caller field nor
a model's owner-looking text can supply it. Recipient-free calls do no identity
I/O. This reader does not prove link inertness, template pinning, a standing rule,
actuation risk or any permission to bypass the existing exposure matrix.

`PublishedAuthorities` also consumes that shared identity reader for exactly the
native `core.notifications.notify` descriptor from the complete current fleet.
The actual final registration, publication digest and admitted goal remain bound;
renamed/foreign descriptors and caller-supplied resolver or owner fields cannot
select this branch. The same locked connection reads identities before budget
reservation and the existing exposure/approval matrix still decides the call.
An exposed owner-directed notify parks because this reader proves no inert
rendering. Unknown or inherited parties park; unavailable owner storage refuses.
Ordinary transport success remains an UNKNOWN effect with no committed receipt.
The new published-catalog/identity PostgreSQL control is authored only; software
SQL-protocol stores do not prove role, lock or durability behavior.

Software controls exercise actual Broker.call and loopback private MCP. The new
PostgreSQL identity/retirement-lock control is authored and collected, not run
locally. These controls do not establish owning durable receipts. Actual A2
schema/producer/executor integration still requires the delivered S1 approvals
chain and its serialized migration/foreign-hunk custody. The verified current
catalog factory still requires delivered M1 publication plus explicit per-resource
selection-bound metadata (bu-n7zmbp.6); omission is never converted to false.
The isolated production startup, genuine runner/session inputs, owning standing-rule/risk adapters, exposed owner-notify inertness and
positive UnitB/replay remain required. The runtime stays dark/default OFF; this
increment does not complete the original nine acceptance criteria.


## Ordinary signed MCP forwarding

`SignedMCPDomains` is the concrete `Domains.dispatch` implementation consumed by
`Broker.call`. The isolated control owner supplies a fixed domain-to-MCP-endpoint
mapping; tool arguments cannot select an endpoint. Endpoints require an explicit
port and `/mcp` path. Plain HTTP is accepted only on literal loopback addresses;
remote endpoints require HTTPS with normal certificate verification. Constructing
this adapter neither starts a listener nor enables the default-OFF profile.

Each forward snapshots and digest-checks the exact arguments before its first
await. It sends the native tool name to the configured owner and carries the
broker's `X-Butlers-Assertion` unchanged. For a `key_required` effect,
`Idempotency-Key` is exactly the effect UUID covered by that assertion; no key is
injected into or substituted for the raw arguments. Downstream D7 verification
and the owning handler's idempotency/receipt implementation remain required.

The adapter performs one standard Streamable HTTP MCP initialization and one
`tools/call`. An initialized stateful session ID is scoped to that call and exact
endpoint. Both JSON and SSE replies are read under a one-MiB raw-byte ceiling;
requests have a 256-KiB encoded ceiling. Compressed responses, duplicate JSON
names, malformed or mismatched RPC identity, redirects and incomplete SSE
refuse. The client does not use environment proxies, follow redirects, retry
HTTP requests or reconnect/resume an interrupted event stream. The active
exchange has a twenty-second bound plus a separately bounded five-second client
cleanup, within the broker's existing thirty-second forward bound. Cancellation
retains the one owned exchange/cleanup task through its bound and propagates
cancellation afterward. Incomplete cleanup is explicit and retains the handle;
it is never successful settlement.

A successful read returns the validated MCP result after central settlement and
output-exposure persistence. A read error returns no content. An effect's generic
MCP success, error, `structuredContent`, or text claiming a receipt is always
UNKNOWN here, with no value or trusted-output stamp. Only a future actual owning
committed-receipt producer can establish effect commitment. The existing central
duplicate-digest check refuses a subsequent UNKNOWN effect; this adapter never
re-dispatches it. PARK has no ordinary-MCP fallback: it remains the separate
purpose-bound owning approvals consumer.

The two-listener software control exercises the actual session HTTP endpoint,
broker reservation/signing, ordinary stateful FastMCP HTTP/SSE handler, and
settlement, including independent signature/raw-argument verification at the test
peer. It uses controlled central storage and ephemeral test signing material;
it proves neither migrated PostgreSQL nor a deployed domain verifier or durable
effect receipt. Actual M1 current-fleet delivery, owning selection/policy and A2
receipt producers, the runner/profile, full nine-criterion runtime acceptance,
current hosted/protected delivery and activation remain separate obligations.


## Owning standing rules at PARK

The internal `park_goal_call` producer can pass its final `ToolCatalog` and
already verified `Grant` to `OwningGoalApprovals`. These are infrastructure
inputs, not wire fields or a substitute for authenticated D7 ingress. Missing
context retains the existing pending-only admission. No transport is enabled.

After the existing finite schema/role checks, same-schema serializer and goal
gate lock, the repository locks actual owning `approval_rules` rows and uses the
existing deterministic matcher and conditional use claim. It commits the pending
action's approved digest/rule, approved gate, immutable receipt and audit events
in the same connection/transaction. A receipt failure rolls back the use claim
as well. A lost commit reply can be resolved by current LOOKUP; an exact repeat
PARK returns the historical parked alias without consuming another use. Stop
cancels an undispatched approval and does not refund an already issued rule use.

Park-only grants, `always_per_event`, third-party starts, child administration
and exposed noncommunication effects cannot use this path for automatic
approval. Safety arguments must be pinned. Exposed communication additionally
requires either the actual native notify or Messenger email same-transaction owner-recipient
binding or exact constraints fixing every declared and supplied argument.
This implementation supports the exact-value branch of template pinning only;
it does not infer owner-authored typed templates from strings or patterns.

Software controls exercise real registration, rule matching and the owning
repository with a transaction-capable SQL stand-in. New PostgreSQL controls
cover committed response loss, rule/receipt rollback, Stop, and simultaneous
exhaustion/revocation with observed backend waits and separate readback. Those
new bodies are authored for hosted execution, not local PostgreSQL proof.
Neither a rule approval nor its receipt is a dispatch/committed-effect witness.
The actual verified ingress, current catalog/grant wiring, approved replay,
owning dispatch witness, runner integration and remaining full-nine delivery
requirements remain separate work.


## Explicit trusted central runtime

The `butlers control --profile TRUSTED_BROKER --config <switchboard-directory>
--broker-config <operator-runtime.toml>` command selects a separate isolated
process profile. It does not change ordinary daemon startup or the existing
`KEY_ABSENT_STAGE` refusal listener. Omitting the broker config, or leaving its
`enabled` value false, refuses this selected broker process before creating a
daemon, opening private material, querying a broker pool, or binding a socket.
No supplied configuration is enabled by default and no deployment is changed.

The operator TOML is a flat strict shape: boolean `enabled`, absolute `key_file`,
string `kid`, a `verification_keys` table mapping one or two current/next IDs to
canonical base64 raw Ed25519 public keys, and a `domain_endpoints` table naming
every current fleet domain. Each fixed endpoint obeys the signed transport's
existing HTTPS-with-port or loopback-HTTP-with-port and exact `/mcp` contract.
It is not selected from an MCP argument, callback, header or redirect. The config
reader accepts only a bounded regular non-symlink file (64 KiB), uses literal TOML
without ENV expansion, and refuses unknown keys or malformed shapes even OFF.
Unused OFF key paths and string placeholders are never resolved or opened.

After the actual Switchboard daemon starts, `build_runtime` takes its existing
`daemon.db.pool`; it creates no second pool. One finite transaction verifies the
real Switchboard runtime role and the complete current fleet through
`PublishedAuthorities`. The factory then loads the explicit immutable file with
`AssertionSigner.from_file` (current euid, mode 0400, regular non-symlink PKCS8,
recognized matching public key). There is no private key environment lookup,
CredentialStore fallback, provisioning or reload. Both control profiles still
reject private-key environment names and forbidden DB-store key metadata. Only
the key-absent profile additionally forbids matching mounted-file names entirely.

The factory fixes `PublishedAuthorities`, `SignedMCPDomains`, the file signer,
`PrivateApprovals` with the explicitly configured owning peers, and the actual
`BrokerSessionListener`. Each listing/call
continues to lock and authenticate its own goal/invocation, stored grant, lease,
lead epoch and current published catalog. A factory preflight is not a cached
authorization snapshot. The session mounts only the authenticated goal MCP
endpoint; tokens are issued only by the existing private committed-invocation
runner hook, never through a new HTTP route. Actual runner integration is still
missing. Domain success text remains UNKNOWN for effects, never a committed
receipt or permission to retry an ambiguous effect.

An empty owning client map leaves PARK unavailable. Configured peers use the
fixed signed-purpose/019 operation path, not DTO or mTLS-only authority. Positive
PostgreSQL replay and receipt proof remain distinct from this source assembly.
General receiver-side D7 verification and runner work remain separate owners.
Selection, risk and undeclared recipient authority are refused.

The central listener retains its existing same-task FastMCP lifespan and six-second
close bound. The control process owns one retained teardown task and one absolute
budget: that native six-second bound plus the daemon's configured shutdown/drain
budget. Repeated cancellation cannot reset it or skip daemon release after a
listener failure. A task still pending at the deadline is cancelled, retained on
the daemon and reported as incomplete; abort/cancellation is not settlement proof.
The daemon pool is released after the session listener. Shutdown errors and the
original operation error/cancellation remain visible.

Software consumers exercise real registered publication/reader adapters, ephemeral
file signing, actual TCP/FastMCP forwarding and lifecycle cancellation against
explicit stateful SQL protocol collaborators. They do not establish database
roles, row locks, durable commits, actual creator authority or production domain
replay protection. The migrated runtime-role HTTP/readback test is authored for
hosted PostgreSQL and is not locally executed. Full RFC0038 nine-criterion delivery,
actual runner admission, owning SQL proof, positive effects and live provisioning remain unearned.

### Owning Grant and approved-call handoff

The private approvals successor uses a separate signed purpose envelope, not an
ordinary D7 assertion or the model request's lineage. Central reservation selects
and commits the exact Grant, original argument digest and PARK call identity
before sending it. The envelope binds the current catalog and owning registration,
original tuple, declared approval mode and operation. The owning daemon verifies
its fixed control peer, current/next signing key, signature, expiry, purpose and
final registered descriptor before entering the 019 transaction. The existing
central `allow_standing_rule` fact is carried descriptively as `central_rule_match`;
it is never changed to manufacture matching evidence. Actual owning rule selection
uses the authenticated selected Grant, final catalog and same-transaction rules,
recipient binding and exact-value restrictions. Park-only and always-per-event
calls cannot autoapprove. Explicit authenticated human rules for exposed calls
remain allowed where their actual mode and constraints permit them.

An owner decision locks goal serialization and the owning gate before the action.
Displayed-call validation, edits, decision, rule and audit retain their existing
owning transaction. Goal approval does not enter the ordinary executor. A bounded
central discovery worker reads approved snapshots after releasing its pool,
reauthorizes the stored original/edited call and commits a central dispatch intent.
The owner then commits an exact snapshot witness. If that reply is lost, only a
fresh authenticated witness read may recover it. Central attachment must commit
before one EXECUTE request. EXECUTE atomically consumes the same immutable intent
and snapshot under the owning lock, commits, and only then enters the captured
registered original through the shared task-bound approval execution context.
Concurrent or repeated requests cannot consume again. A lost consumption reply or
crash after commit stays UNKNOWN and cannot authorize a second forward.

No handler result, MCP text, approval receipt or local consumption timestamp proves
an external effect committed. Owning SQL races, roles, trigger behavior, rollback
and lost-reply durability require genuine PostgreSQL execution; software protocol
controls do not provide that proof. Production remains default OFF. Genuine runner
invocation admission, generic domain D7 replay verification and authoritative
effect producers retain their separate contracts and remaining delivery work.

### Calendar D13 preparation boundary

Native Calendar responses enter canonical owning PARK through the final
registered `CalendarResponseCoordinator`, with provider preparation outside
central/owning locks and prepared command plus goal gate/action committed on
one owning connection. The original P/effect identity survives server preparation
and subsequent owner semantic edits. Calendar remains `always_per_event` even
for an unexposed goal or a park-only Grant; standing rules cannot approve it.

A distinct default-OFF dashboard-owner mTLS service can append a candidate, but
only the authenticated API's independent current central Grant/catalog read and
owning goal-first locks/CAS/audit may attach it. No generic callback parser,
caller-selected private operation, ordinary MCP identity or candidate reply is
authority. The refreshed safe preview is derived from the selected stored
command and its displayed-args digest; unavailable previews are Open only. A
new explicit approval binds A′. Existing stored replay then requires the same
current authority and one-winner consumption before the original handler; C′
receipt/inverse selection never grants a second forward after ambiguity.

The local software transport/transaction controls are not PostgreSQL or provider
proof. Migrated runtime-role Calendar edit, race, rollback and independent
readback cases are authored for hosted execution. No local activation, new
selection/recipient authority, generic D7 verifier or full broker nine-criterion
completion follows from this source increment.

### Native Messenger email

The complete published Messenger `email_send_message` descriptor has one native
recipient branch: module `email`, group `write`, namespace `email.write`, explicit
`to` resource with `selection_bound=false`, and the actual configured approval
gate. Changed or foreign metadata cannot borrow that branch. On the existing
locked transaction, the adapter reads a unique live owner and invokes the
existing ambiguity-safe owner-channel definer for one well-formed email address.
Display strings, multiple recipients, unknown, conflicting or retired identities
remain non-owner; unavailable identity infrastructure refuses before reservation.
There is no implicit email recipient or owner authority derived from message text.

The configured gate still parks owner-directed email, including exposed calls.
Owning rules may approve an exposed call only with the current same-transaction
owner binding or exact pins covering every declared and supplied input; safety
arguments stay pinned. Park-only and always-per-event take precedence. Signed
PARK persists the exact call before any send. Stored or edited approval replay
rereads current catalog, Grant/resources and recipient classification, then uses
the existing goal-first snapshot, audit, Stop and one-winner consume protocol.
An edited `to` has no inherited recipient or resource authorization.

After consume commits, the captured registered EmailModule handler still checks
the real `email.send` permission before its single SMTP attempt. Idempotency stays
`none`; handler output and lost acknowledgement both remain UNKNOWN external
effects and never authorize another forward. Software controls use actual
registration, daemon factory and handler with controlled SQL/SMTP collaborators.
Migrated role, identity ambiguity/owner-change locking, rollback, edited replay,
Stop, permission and competing/lost-ACK cases are authored for hosted PostgreSQL
execution, with local collection providing no durability claim. This branch
does not complete the original nine criteria, enable a profile, or supply generic
D7, selection, creator or runner authority.

## Native no-party administration

The published authority accepts `administer` only when the actual current
registration declares no recipient names, owner default, unknown recipient or
conditional inherited party. It supplies `recipient=none`; it supplies no
standing-rule, selection, rendering or actuation-risk evidence. Missing owning
approvals refuse before reserving a call. Child and third-party administration
remain unavailable.

Every grantable administrative call requires central PARK, even when its actual
registration has no configured gate and the goal is unexposed. Home's registered
`schedule_create` exercises the current publisher, broker and signed owning
handoff: central reservation commits before the owning action, gate and receipt;
the captured scheduling original is not invoked at PARK. D13 still permits a
legitimate unexposed owning standing rule to approve that durable action. Exposed
administration, park-only Grants and always-per-event modes remain pending and
cannot consume such a rule. PARK is therefore not by itself evidence of a human
tap. After an owning decision, authenticated stored or edited replay rechecks the
current catalog, Grant, resources and Stop state, then consumes the exact owning
dispatch once before invoking the captured original. Unknown execution results do
not grant a retry.

Software controls exercise the actual Home registration with explicit SQL and
transport collaborators. Separate migrated central/Home role, immutable history,
rollback, duplicate, Stop, catalog-drift and stored/edited scheduling readbacks
are authored for hosted PostgreSQL execution; local collection earns no database
outcome. This administration increment supplies no Home risk, current selection or inert
owner-notification producer, no generic D7/creator/runner authority, and no
activation or whole-broker completion.

## Native fixed Home scene

Only the complete current Home `home_assistant.core.ha_activate_scene`
registration receives the internal native-scene discriminator. Its explicit
`entity_id` remains subject to the current Grant's exact resource ceiling.
The broker consumes the same pure `ActuationRisk` enum, map and classifier that
Home uses: fixed `scene.turn_on` is consequential, and unknown services remain
protected. Neither arguments nor descriptive labels supply a risk decision.
Generic `ha_call_service`, nested targets, party-bearing actuation and current
selection remain unavailable to this adapter.

Consequential, protected and unknown actuation requires central per-event PARK;
the owning gate independently refuses standing-rule approval for the native
scene, including unexposed owner calls. This does not change the legitimate
unexposed administration rule described above. Approval stores the public scene
arguments. Replay rechecks the current publication, Grant, resources and Stop
state, then consumes the owning dispatch once before entering the original
registered wrapper. The wrapper verifies the exact supplied arguments before
defaulting: omitted `transition` and explicit null have different approval
digests. Only then does it derive the fixed `scene.turn_on` command for the same
task, restoring the outer execution context in `finally`. Other tools, changed
arguments and inherited child tasks receive no such authority.

Home's existing receipt precedes the HTTP request. A successful HTTP response
does not verify a scene's physical outcome; ambiguous responses remain
unverified and do not authorize retries. Software controls exercise actual
registration, publication, signed owning PARK and stored/edited original replay
with explicitly controlled SQL and transport collaborators. Separate migrated
central/Home role, receipt/readback, race, Stop, rollback and reply-loss cases are
authored for hosted execution; local collection proves no PostgreSQL outcome.
This supplies no generic Home risk or selection producer, inert-notification
authority, activation or whole-broker completion.
