## Context

Bearer material (OTP codes, reset links) is scrubbed at ingest. The sensor works from metadata only and never reads, stores or logs a body, code or link. `auth_artifact_observed` (bu-q7vx1q.20) stays a separate deferred slice; the packet's `auth_artifact_requested` kind is therefore not produced.

## Decisions

- **Provider authentication**: exact first-party sender addresses only (no domains, no subdomains). `Authentication-Results`, when the connector kept it (Gmail full tier), rejects a reported DMARC/DKIM/SPF failure and sets the sender-verification verdict to `authenticated` for a DMARC pass or an aligned DKIM pass. Without the header the verdict is `unverified`. This authenticates the provider sender, not the owner. Only `authenticated` events may ever interrupt the owner; `unverified` is a quiet record, so a forged alert cannot page at night. Authentication is a necessary condition for interruption, not a grant to notify or bypass admission, exposure or approval policy.
- **Placement**: the classifier runs in the ingest tool next to, not inside, global policy evaluation, so no global rule outcome changes the event for an ingested alert. The connector-side evaluator carve-out keeps a global `skip` from dropping the mail before submission. Connector-scope `block` rules are owner-authored filters, retain precedence and are left alone; this packet does not publish an event for mail blocked before ingest.
- **Dedup**: ingest dedupe prevents a second ingest row; the publisher also checks (provider, kind, external_event_id) against `public.domain_events`.
- **Answer door**: `no` keys a fleet case on `account_security:<event_id>`; a repeat reuses exactly one case and one evidence row with the provider's static recovery door. `yes` neither opens nor closes a case. The answer is the owner's answer under the existing owner-verification contract, not a claim authenticated by the alert's security label.

## Goal-runtime boundary

[RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md) D1/D2 keep fixed classification, policy evaluation, deduplication, publication and answer recording deterministic. These operations do not start a reasoning session merely because an event is labelled security-related. The event declaration creates no implicit subscriber or grant. A future consumer must be explicitly declared; its model judgment or owner-facing composition is an admitted goal, not a new D18 model-function exception. No child may bypass the control plane to admit or route reasoning.

Under D5, admission derives origin and cause from server-held context, never from the alert's label, payload or model output. A security label authenticates no sender. Email is owner-origin only when RFC 0017 authorizes both channel and sender; provider authentication alone leaves it third-party. A declared subscription admits an obligation under its declared capability set, not under authority inferred from a security label. If an effect-produced event causes admission, the goal carries `caused_by_goal` and `caused_by_effect` from assertion lineage, inherits the causing goal's exposure, and uses the subscription set intersected with the causing goal's origin ceiling. The configured causal-chain limit (default 2) is enforced by refusing and recording over-limit admissions.

Under D6, exposure can only increase. Third-party ingest starts exposed; quoted or forwarded spans and exposed runtime-created obligations retain their exposure rules. A domain-published input without goal lineage starts its goal exposed unless the publisher stamps it as derived only from untainted owner-authority records; a missing or unreadable stamp fails closed as exposed. The security label and sender-verification verdict neither clear exposure or stored taint nor upgrade origin, enlarge the origin ceiling or grant capabilities. Existing broker checks and tool approval policies still apply. These lineage and stamp rules use the adopted runtime's admission context; they add no message content or authority-bearing fields to the fixed security-event payload.

Under D10, admission reserves the finite goal budget for invocations, child concurrency, tool calls, model tokens/spend and deadline, with invocation turn/token limits. Delegation and calls decrement it atomically; exhaustion refuses new work and ends the goal `budget_exhausted`. Budget tuning bounds work and cannot widen authority. This is a contract for a future declared reasoning consumer, not delivery of one: the interactive prompt, counter/header work, estate registry feed and manifesto ownership decision remain deferred in tasks 4.1–4.3.

## Risks

- A crash between the ingest commit and the publish loses one event (best-effort publish, as for the fleet-event fan-out).
- A provider subject change is a false negative; unclassified allowlisted-sender mail is not counted yet.
