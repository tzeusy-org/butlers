# Capability broker transport foundation

The daemon's dedicated private transport is default OFF. It is separate from the
ordinary model-presentable MCP catalog. This foundation does not implement the
owning approval producer, cancellation worker, authenticated Stop route, runner,
or approved replay executor.

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
