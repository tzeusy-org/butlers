## Why

A lost, stolen or replaced owner channel still resolves to the owner. Through
`public.resolve_owner_triple` it can answer Telegram approval callbacks, stamp Switchboard ingress
as owner, and receive owner-recipient handoff without approval. Nothing records that an endpoint has
left the owner's custody. Bead bu-s11n0s.1 carries the full released endpoint-custody outcome
(cheap owner-resolved LOCK, protected release, provider-wide fallback, egress fallback, security
question, Finance and recovery evidence); its design field holds the adopted protocol and all eleven
proposed requirements.

## What Changes

This change lands that outcome in serial slices. Its spec carries the two requirements the first
slice starts, copied verbatim from the adopted design (REQ-endpoint-custody-holds-002 and -006).
Later slices add the remaining requirements (-001, -003 to -005, -007 to -011) as they land, so no
requirement appears here before work on it starts.

Slice 1 (core_293):

- `public.custody_holds`: one authoritative hold record per canonical owner endpoint target and
  monotonic generation, keyed by `public.custody_target_digest` over the typed fact universe of
  `resolve_owner_triple`. History is append-only; the only permitted change is one release stamp.
- Row level security with no policy: no runtime role reads or writes holds, despite bootstrap
  public DML regrants. The database owner maintains the table, as in core_275.
- `public.custody_target_held`: content-blind SECURITY DEFINER eligibility read.
- `public.resolve_owner_triple` refuses owner for any sole-owner match with an actively held fact,
  so every owner-only definer consumer fails closed. Ambiguity, inactive, merged and deleted rules
  are unchanged.
- Downgrade refuses while a hold is active, or when the executing login cannot see every hold.

Slice 1 installs no runtime writer. Holds are written only by the trusted database owner until the
admitted Switchboard engine lands.

Slice 2a (core_347) starts REQ-001, -003 and -004, now copied verbatim into the spec, and lands
their protected half:

- `public.custody_commands`: the immutable receipt of each committed command, keyed by the
  caller's command id with a digest of its canonical request (replay returns the stored result; a
  reused id with another request is refused; a lost acknowledgement is read back by id).
- `public.custody_engine_apply`: hold, release and replace commit with the episode's fleet case,
  content-blind evidence and the receipt in one transaction. A hold names owner channel fact ids;
  each must resolve to the owner alone. Release and replace compare the expected generation;
  replace refuses while the owner still holds the binding.
- `public.custody_dashboard` (EXECUTE for `dashboard_auth_api` only) checks the current cookie
  session and CSRF token under the `dashboard_auth` singleton lock in the committing
  transaction. `public.custody_host` has no grant; the host's administrative login runs it.
- Dashboard routes under `/api/custody` and the host commands `butlers custody ...`.

The slice 2 scoping (2026-11-17) places the protected writer in a database-owner definer that
checks owner auth in the function, rather than a Switchboard service call: REQ-002 lets
dashboard/host control be verified at its owning boundary and forwarded as a protected command. The cheap owner-resolved LOCK and the REQ-001 provider fallback (task
2.1b) and P-CUSTODY-1c (task 2.1c) wait for the RFC 0038 cutover; P-CUSTODY-1c stays UNDELIVERED.

Slice 4 starts REQ-007, now copied verbatim into the spec, and lands the Messenger egress fence
with the owner-default fallback (tasks 2.3a and 2.3b):

- `butlers.core.custody_egress`: a fail-closed check over the content-blind
  `public.custody_target_held` read. It adds no table, role or function.
- Fenced paths: email send/reply refuses a held recipient or cached sending account; Telegram
  send/reply, files, edit and react refuse a held recipient chat; the chronicler gap interview,
  which posts to the owner chat directly, is not delivered. Registered tools, route.execute, the
  approval-card handoff, approved execution and deferred flush reach Messenger's adapters
  through those methods. Connector replies stay guarded by `resolve_owner_triple`.
- Follow-up bu-s11n0s.93 fences the informational confirm edit and WhatsApp send/reply
  (recipient JID/phone and the bridge account JID/phone; unknown fails closed).
- Known unfenced: Google Calendar invitations with `sendUpdates=all`, which are outside
  REQ-007's Messenger scope (bu-s11n0s.94). Username-form Telegram holds do not fence chat-id
  sends (documented limitation).
- notify's owner default moves from a held or unreadable Telegram target to the owner's unheld
  email, or returns `endpoint_held`/`eligibility_unknown` with a durable ledger row and no
  provider attempt.

Messenger delivery and the approvals executor both survive the RFC 0038 cutover (D7 and the
legacy `pending_actions` rule), so the fence does too, and it adds nothing to classification,
routing or delegation. The executor-side source recheck waits on P-CUSTODY-1c (task 2.3c).

Slice 3a starts REQ-005, now copied verbatim into the spec, with D3's amendment of
REQ-module-memory-013 (task 2.2a):

- Switchboard's identity step checks custody for an owner-looking sender
  (`butlers.core.custody_ingress`, over the owner definer's typed candidates, fail closed). A held
  or unreadable endpoint keeps the owner entity for attribution and sets
  `source_sender_custody = endpoint_held` in the route envelope; only that server-side read sets
  it.
- Memory stamps such a session `third_party` with `custody_reason = endpoint_held` in row
  metadata, so its content is an attributed report that cannot steer. Release changes no stored
  row.
- The other owner-authority stores honour it: the Relationship accepted-source report freezes
  `third_party` (an identity claim is held, not auto-confirmed), Finance's expected-signal
  producer gives no owner signal, and the routed prompt preamble marks the endpoint held.
- Owner-ingress engagement skips it; break-through resolves it to no entity (narrow only).

This is a security fix inside the frozen classify-and-route path (RFC 0038 "Freeze" admits
security fixes). At cutover owner origin comes from owner-channel authorization, which
`resolve_owner_triple` already refuses for a held endpoint, so the Switchboard part is superseded;
the memory scenario is carried in the generalist change's REQ-module-memory-013.

## Impact

- Code: `alembic/versions/core/core_293_endpoint_custody_holds.py`,
  `alembic/versions/core/core_347_custody_command_engine.py`,
  `src/butlers/api/routers/custody.py`, `src/butlers/api/owner_auth/custody_cli.py`,
  `src/butlers/core/custody_egress.py`, `src/butlers/modules/email.py`,
  `src/butlers/modules/telegram.py`, `src/butlers/core_tools/_routing.py`,
  `src/butlers/core_tools/_notifications.py`, `src/butlers/core/approval_delivery_transport.py`,
  `src/butlers/chronicler/gap_interview_telegram.py`, `src/butlers/core/custody_ingress.py`,
  `roster/switchboard/tools/identity/inject.py`, `roster/switchboard/tools/routing/contracts.py`,
  `roster/switchboard/tools/insight/engagement.py`, `src/butlers/modules/pipeline.py`,
  `src/butlers/core_tools/_switchboard.py`, `src/butlers/switchboard_wiring.py`,
  `src/butlers/core/break_through.py`, `src/butlers/modules/memory/content_authority.py`,
  `src/butlers/modules/memory/storage.py`, `src/butlers/core/fact_authority.py`,
  `roster/finance/tools/expected_signals.py`.
- Specs: this change's endpoint-custody-holds spec and a MODIFIED REQ-module-memory-013 (D3);
  the same scenario is carried in generalist-lead-focused-delegation's REQ-module-memory-013.
- Tests: `tests/migrations/test_resolve_owner_triple_migration.py`,
  `tests/migrations/test_core_347_custody_command_engine.py`, `tests/api/test_custody_router.py`,
  `tests/cli/test_custody_cli.py`, `tests/core/test_custody_egress.py`,
  `tests/migrations/test_custody_egress_fence.py`, `tests/daemon/test_notify_custody_default.py`,
  `tests/chronicler/test_gap_interview_telegram.py`, `tests/core/test_custody_ingress.py`,
  `tests/modules/memory/test_content_authority.py`,
  `roster/switchboard/tests/test_owner_ingress_engagement.py`,
  `tests/core/test_fact_source_capture_liveness.py`,
  `tests/integration/test_relationship_fact_content_authority.py`,
  `roster/finance/tests/test_expected_signals.py`,
  `tests/daemon/test_route_execute_async_dispatch.py`.
- Docs: `docs/identity_and_secrets/owner-identity.md`, `docs/modules/approvals.md`,
  `docs/data_and_storage/schema-topology.md`, `docs/modules/memory.md`.
- No security.md, manifesto or RFC amendment: the design's authority amendments stay unadopted.
