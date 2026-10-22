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
