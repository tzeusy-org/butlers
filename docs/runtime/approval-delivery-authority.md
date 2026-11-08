# Approval delivery authority

This document describes the dormant approval-only transport implementing RFC
0023. The required behavior remains in the
[delivery-intent recovery capability](../../openspec/specs/approval-delivery-intent-recovery/spec.md).
Constructing the transport does not enable admission. The trusted cohosted
launcher installs runtimes and workers only for active sources whose
server-held worker flag is enabled. Rollout remains disabled by default.

## Applicability and trust boundary

`ApprovalAuthorityTopology` is an explicit trusted-host composition for Linux
daemons cohosted in one process, as in `cli._start_all`. Its private companion
MCP listeners expose only `deliver`, `route.execute`, or the source verifier's
`verify_approval_admission` and `verify_approved_notify`, using the actual
registered tool wrappers. Every
inter-butler request still crosses MCP; no peer pool or SQL read is shared.

The listeners use Unix sockets and Linux `SO_PEERCRED`. Before parsing HTTP,
they require the peer PID to equal the process that constructed the listener.
The trusted host fixes the issuer and audience for each listener and installs
that provenance in the ASGI scope. Request headers, access-token DTOs, source
selectors, registry URLs and tool arguments cannot install this principal.
A same-UID child has a different PID and is refused, even if it knows the
socket path. Socket permissions do not provide same-UID secrecy or isolation.
Trusted code inside the cohosted process is part of this boundary.

Independent-process deployments and platforms without `SO_PEERCRED` have no
approval authority through this implementation and fail closed. Public TCP MCP
continues to support ordinary notifications; absent or null recovery is
ordinary, while every non-null recovery field requires protected authority.
This protocol does not add authentication to other MCP tools or provision
persistent keys, credentials or a global trust root.

## Source admission and delegation

`SourceApprovalAdmission` lives beside the owning
`ApprovalDeliveryRepository`. Its mint method is private deterministic source
code, never an MCP tool. It takes the worker's local fenced claim and exact
notification. `authorize_transport` checks the actual owning schema and
`SET ROLE` identity, live handoff-started lease/token/fence, immutable subject,
presentation generation/key/mode, and the admitted pending action or eligible
cohort-member relationship. The stored provider-start attempt must match the
current fence for handoff; a higher reclaimed fence permits reconciliation
only. A private claim's operation flag cannot override that durable boundary.
A terminal or expired action cannot mint or verify
send authority. A successor presentation created by defer excludes an old
generation from a new handoff delegation. Reconcile retains the old tuple and
cannot perform a speculative resend.

After that check, the source creates a random ephemeral proof bound to the
claim, operation, complete notification digest and source epoch. Proofs expire
after at most ten seconds and never outlive the claim's remaining lease.
Revocation removes the proof; retirement clears all admissions. A replacement
source has a fresh epoch and cannot verify old proofs. The source verifier
rechecks the owning durable relationship for every request.

The proof travels only in `X-Butlers-Approval-Proof` on the bootstrap-bound Unix
HTTP connection. It is absent from URLs, tool arguments, model prompts,
notification envelopes, receipts, audit and metrics. Clients disable proxies
and redirects. Switchboard calls the source's separately protected verifier
over MCP; it cannot choose an endpoint from caller input or read the source
schema. The verifier audience admits only the trusted Switchboard ingress.
Unavailable verification, missing or invalid proof, stale epoch, digest or
tuple mismatch produces the same content-blind refusal before generic
notification logging, Messenger ledger writes or provider calls.

Only then does Switchboard construct the internal trusted correlation and
route through the registry to its bootstrap-bound protected Messenger client.
Messenger independently requires the protected Switchboard audience before
reading the internal DTO. Its existing ledger owns provider-start markers and
deduplication for the issuer/schema/presentation/mode tuple. Confirmed duplicates
reuse the result. Telegram, email and WhatsApp uncertainty after provider start
stays ambiguous. Reconciliation uses the original tuple and provider capability;
an unavailable reconciliation capability never authorizes a blind resend.

## Approved-notify proof (RFC 0038 D13)

An owner-approved `notify` must reach its recipient once, never parked a second
time by Messenger's recipient gate. The dashboard API therefore no longer sends
an approved `notify` itself: it dispatches the action to the owning butler's
`dispatch_approved_action`, like every other approved tool. The owning daemon's
executor holds the approved row (null result) and sends the stored call's
canonical `notify.v1` envelope (`approved_notify_request`) without re-running
`notify()`.

Where this topology is installed for the source, the executor uses
`SourceApprovedNotifyDispatch`. `SourceApprovalAdmission.mint_approved_notify`
is a separate operation from the `approval_request` admission and never widens
it or shares its store. It reads the live row through
`ApprovalDeliveryRepository.approved_notify_args` (owning schema and role,
`tool_name = 'notify'`, `approved`, null result, no goal lineage), checks the
stored-call digest and requires the presented envelope to equal the one projected
from that row. It refuses recovery correlation, a foreign origin and owner-control
intents. The proof binds action id, stored-call digest and envelope digest,
expires within ten seconds and is revoked after the call whether or not it was
presented.

The proof travels in the same `X-Butlers-Approval-Proof` header on the source's
kernel-admitted Switchboard companion. A header on public TCP has no principal and
is ignored, so that delivery stays ordinary. On the protected hop Switchboard has
the source's verifier `verify_approved_notify` consume the proof. The first
presentation consumes it whatever the outcome. The verifier rechecks expiry,
the envelope digest and the live row. Switchboard then requires the principal,
`source_butler` and `origin_butler` to name the same source. Any failure refuses
content-blind (`Approved notification authority rejected.`); it never degrades to
an ordinary, re-parkable delivery.

Switchboard re-digests the envelope it actually forwards, after its own rendering
facts, and folds an `ApprovedNotifyAttestation` (issuer, action id, digest) into
Switchboard-only route context for its protected Messenger client. Messenger
parses the attestation only when the protected Switchboard principal installed
the request, binds it to the handling task, and lets the shared approval matcher
(`get_approval_execution_context`) honour it. It is honoured only for
`route.execute` whose routed `notify_request` hashes to the attested digest,
names the issuer as origin and route sender, and carries no recovery. A bare
field, a caller origin or an action id grants nothing.

The deliver hop nests two protected calls (the source verifier, then Switchboard
to Messenger), each bounded by the ten-second companion deadline. Its own deadline
(`approved_notify_deliver_deadline()`, 25 s) outlasts both and stays inside the
dashboard API's 30 s dispatch deadline, so a late but successful send is never
reported as a failure. If the hop still times out, an unconsumed proof proves
nothing was forwarded and the row stays retryable. A consumed proof means the send
may have happened. The executor then records the action executed with
`{"status": "outcome_unknown"}` instead of leaving it eligible for a Retry that could
message a non-owner twice.

Without this topology (single-daemon `butlers run`, split hosts, or a source
whose worker flag is off) the executor falls back to an ordinary `deliver`.
Messenger's recipient gate then applies and parks a non-owner send as the native
channel command, which renders its preview and replays once on approval.

## Composition and cleanup

The opt-in topology receives already registered source, Switchboard and
Messenger objects and the expected registry target. Owned source registration
places privileged preauthorization outside the actual registered FunctionTool
proxy, on both public and protected ingress. Admitted recovery and ordinary
calls retain the original span, logging and sanitized capture instrumentation;
rejected recovery does not enter those wrappers. Tool group exclusions remain
unchanged. The companion copies this guarded registered tool, not an unwrapped
business handler. `runtime(source, ...)`
returns a dormant `ApprovalRecoveryRuntime` with the protected dispatch; it
does not attach it to a daemon itself. After all successful daemon registrations,
`cli._start_all` enters `approval_delivery_composition`. It discovers active,
enabled Approvals modules and reads each schema's server-held worker flag.
Disabled, missing or invalid flags create no admission objects, socket directory,
listeners, runtimes or workers. Enabled sources require both registered
Switchboard and Messenger in the same host; missing peers or tool registrations
fail closed without a TCP fallback. A source whose configured schema differs
from its butler name is unsupported and remains unavailable.

The composition uses each source module's own repository and binds the expected
Messenger registry target from the registered daemon port and the existing
server-side cross-container URL mapping. The kernel-admitted companion still
uses the registered Messenger object; the URL equality check grants no TCP
authority. The source's existing default owner-recipient lookup supplies a
candidate, which must independently resolve through `resolve_owner_channel_via_definer`
on its owning pool before rendering. Messenger verifies the owner again. Callback
secrets resolve through the source's credential store with environment fallback
disabled; neither resolver value is logged or put in topology configuration.

The host starts each eligible worker only after these bindings exist. On normal
shutdown, startup failure or cancellation it stops all attached workers before
retiring source admissions and listeners, then shuts down daemon modules/pools.
Partial composition failure follows the same cleanup and leaves delivery
unavailable; failed daemon startup is also torn down before retry or cancellation.
Fresh startup has new source epochs, including when registered objects are reused.
Worker-flag changes require a host restart to install an absent topology; module
re-enables can reuse an already installed runtime. Single-daemon `butlers run`
and split-process control/domain hosts do not install recovery authority.
Operational activation, any exclusive temporary dev cohosting cutover and its
canary remain separate rollout gates. This installation neither changes flags
nor backfills or replays historical actions.

Approval companions use stateless JSON request/reply responses, with no SSE
stream or dependency on process-global SSE drain state. Each client call has
a ten-second deadline covering initialization, tool reply and client teardown;
a missing reply fails closed without granting a retry or speculative resend.
The companion's HTTP client disables separate HTTPX phase timers so the
whole-call deadline governs instead of an implicit shorter read timeout.
Both that deadline and HTTPX timeout exceptions normalize to the same
content-blind `RecoveryAuthorityError`; no raw request or error URL is exposed.
FastMCP's connection-initialization `RuntimeError` normalizes only when its
direct explicit cause has a timeout type. This single-hop check uses no error
message matching; unrelated runtime errors and non-timeout causes propagate.
If that deadline expires after provider start, the worker records
`ambiguous/provider_outcome_unknown` under its existing claim fence and clears
the lease without scheduling a retry. The durable Messenger start marker is
retained. Neither a fresh claim nor a new source admission is authorized by
the timeout. The existing original-tuple reconciliation boundary remains;
an expired handoff lease can be reclaimed for reconciliation only, whereas an
already quarantined ambiguous presentation is not automatically reclaimed.
Fresh or reused registered objects can form a new topology after the prior
listeners close. The ordinary TCP listener in the synthetic harness also uses
JSON and a deadline; generic public daemon transport is unchanged.

Closing the topology retires its admissions, clears only its own module
bindings, fences existing connections, closes listeners/descriptors and
removes only socket paths with the inode it created. Setup failure follows the
same cleanup. It refuses existing paths instead of deleting another listener.

## Verification

`tests/integration/test_approval_delivery_authority_transport.py` owns the
registered local source → protected verifier → Switchboard → Messenger →
synthetic provider gate. It uses actual migrations, bootstrap replay, owning
`SET ROLE` pools (with disposable cleanup by the migration owner), actual
generic registration proxies, positive admitted action/cohort controls, adversarial proofs
and PID checks, ordinary TCP compatibility, tuple deduplication and post-start
uncertainty. It never injects `get_access_token` or trusted source DTOs as
authority. Its PostgreSQL cases require actual execution; collection or a
Docker setup failure provides no behavior evidence. Each registered transport
case has a 45-second deadline so missing initialization/replies fail visibly.
The existing kernel-peer node also repeats fresh/reused three-companion
lifecycles, accepts a real six-second reply with production timeout defaults,
and checks an actual nonreplying handler's call deadline; that
PostgreSQL-free scope proves liveness, not source admission or ledger behavior.
The Telegram uncertainty parameter separately holds its synthetic provider
after both real owning-role start markers, observes the actual transport
deadline through the worker, and checks durable same-fence ambiguity, refused
remint and no resend. Its provider-raised-error branch and the original-tuple
reconciliation parameter remain separate controls.

The nearest core, daemon and Switchboard registration files own unsupported
platform and early refusal checks. The planted recovery/privacy test in
`tests/integration/test_approval_push_on_park.py` owns generic notification
list/count/read/ack/retry/escalate, replay envelope and conversation-history
exclusion, with ordinary positive companions. Hosted checks against the exact
source head and independent security/concurrency review are required before
merge. No live provider or activation is part of this source proof.
