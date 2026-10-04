# Approval delivery authority

This document describes the dormant approval-only transport implementing RFC
0023. The required behavior remains in the
[delivery-intent recovery capability](../../openspec/specs/approval-delivery-intent-recovery/spec.md).
Creating this transport does not enable admission, attach a daemon runtime, or
start a worker. The rollout remains disabled by default.

## Applicability and trust boundary

`ApprovalAuthorityTopology` is an explicit trusted-host composition for Linux
daemons cohosted in one process, as in `cli._start_all`. Its private companion
MCP listeners expose only `deliver`, `route.execute`, or
`verify_approval_admission`, using the actual registered tool wrappers. Every
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
does not attach it to a daemon. Normal daemon startup does not construct this
topology. Operational activation and its canary remain separate rollout gates.

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
Docker setup failure provides no behavior evidence.

The nearest core, daemon and Switchboard registration files own unsupported
platform and early refusal checks. The planted recovery/privacy test in
`tests/integration/test_approval_push_on_park.py` owns generic notification
list/count/read/ack/retry/escalate, replay envelope and conversation-history
exclusion, with ordinary positive companions. Hosted checks against the exact
source head and independent security/concurrency review are required before
merge. No live provider or activation is part of this source proof.
