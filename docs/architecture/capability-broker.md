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

This projection does not authenticate a local value as the fleet's current
registration. The genuine M1 current-reader/digest binding, actual owner
selection and policy producers, and owning approval storage/receipt/replay
producer remain required. Mode metadata and policy tests do not implement the
owning always-per-event rule-creation, matching or promotion enforcement. They
do not enable the private transport or activate the runtime.

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

Production startup currently refuses enabled settings with the closed category
`private_transport_prerequisites_unavailable`, before accessing material or
constructing private factories/listeners. A genuine committed A2 producer and a
separately admitted isolated trusted profile are still required. There is no
fallback to public MCP or implicit downgrade to OFF. Successful configuration
parsing is not successful enabled deployment.

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
command still creates only `SessionRefusalListener`. No new deployment profile,
key resolver, ambient broker, public token route, or activation is provided.

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

`CancellationWorker.run_batch()` is an explicitly invoked, sequential finite
consumer. It constructs no transport, background task or scheduler. The isolated
resource owner must provide actual purpose-bound `PrivateClient` instances for
fixed domains. First-generation claims may CANCEL once; reclaimed generations
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
after the consumer is terminal. No automatic retry, lease renewal or release,
new daemon/profile attachment, or global Stop-completed response is introduced.

Real TLS software controls exercise this bridge using explicitly synthetic
owning receipts and a repository control. They establish neither A2 durable
commit authority nor SQL claim/ACK/race correctness. The actual A2 gate/tombstone
producer, authenticated approved replay/executor handoff, isolated production
profile and resource-owner attachment, genuine role/PG/transport races, runner
termination and terminal hosted/protected evidence remain mandatory before
production enablement or full broker acceptance.


## Central stored-approved dispatch transaction

The private `prepare_approved_dispatch` consumer implements the central half of
approved replay against existing sw047/sw048. It is absent from both MCP
listeners. `StoredGoalCall` is data, not proof that an owner approved anything;
the authenticated owning snapshot, local revalidation/dispatch witness and real
executor handoff remain required. `replay_approved` therefore continues to refuse.

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
These consumers perform no domain network request and create no owning receipt.
Actual A2 approval/schema/receipt authority is still blocked on the separately
delivered approvals migration chain; positive UnitB, authenticated owning replay,
real PostgreSQL outcomes and full original broker acceptance remain unearned.

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

Software controls exercise actual Broker.call and loopback private MCP. The new
PostgreSQL identity/retirement-lock control is authored and collected, not run
locally. These controls do not establish owning durable receipts. Actual A2
schema/producer/executor integration still requires the delivered S1 approvals
chain and its serialized migration/foreign-hunk custody. The verified current
catalog factory still requires delivered M1 publication plus explicit per-resource
selection-bound metadata (bu-n7zmbp.6); omission is never converted to false.
The isolated production startup, genuine runner/session inputs, ordinary signed
forwarder, owning standing-rule/risk adapters, exposed owner-notify inertness and
positive UnitB/replay remain required. The runtime stays dark/default OFF; this
increment does not complete the original nine acceptance criteria.
