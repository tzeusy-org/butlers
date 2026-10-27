## Why

New-sign-in, password-changed, 2-step-changed and recovery-changed alerts from identity providers are cut to subject-only by the `noreply` rule, and a promoted rule can turn that into a skip. The owner keeps scanning the inbox for "was this you?" mail. The fleet should perceive these alerts itself without ever touching the codes and links that bearer-material quarantine removes.

## What Changes

- A deterministic classifier (`butlers.account_security`) types first-party provider alerts from sender, subject and, when kept, `Authentication-Results`. No LLM, no body, no bearer material.
- The Switchboard ingest tool publishes one fixed `switchboard.security_event` per ingested alert, independent of the global policy decision. Deterministic extraction and publication remain service-plane work, without a model or goal admission.
- The global-scope policy evaluator demotes a `skip` on a classified alert to `metadata_only`, and rule promotion refuses to propose `skip`/`metadata_only` for the allowlisted senders.
- An answer door records the owner's yes/no per event id; repeated `no` answers open or reuse exactly one fleet case and one evidence row carrying the provider's static recovery door. `yes` neither opens nor closes a case.
- Any model judgment about an alert, including composing an owner-facing notification, requires an admitted goal under adopted RFC 0038 D1/D2/D5/D6/D10/D18. The security label authenticates no sender. Provider authentication and the label establish neither owner origin nor grants; they never clear exposure or enlarge a ceiling. Admission uses server-held origin and cause, preserves applicable event lineage and exposure, and reserves a finite work budget.
- The event contract declares no subscribers until a consumer is declared. This amendment creates no consumer, subscription, grant or model-function exception; the deferred interactive prompt remains future work. Exact-address classification, connector `block` precedence and the existing payload privacy contract remain intact.

## Capabilities

### New Capabilities

- `account-security-perception`: deterministic classification, provider-authentication verdict, fixed event, answer door and the goal-admission boundary for future model judgment.

### Modified Capabilities

- `ingestion-policy`: account-security carve-out for skip rules and promotion.
- `domain-event-bus`: the `switchboard.security_event` contract.

## Owner decisions (not applied here)

- No manifesto ownership amendment is applied here. The earlier options of a General amendment ("steward of ordinary digital life") or a new Steward specialist remain deferred owner decisions. RFC 0038 D1 retains General's narrow capture-and-collections role; this packet does not enlarge it.
- Event type is `switchboard.security_event`, not the packet's `estate.security_event`: the contract registry requires the namespace to equal the publishing butler's roster directory and no `estate` butler exists.

## Goal-runtime conformance

This amends the existing packet before archive to conform to [RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md). It does not implement the goal runtime, archive this change, or complete deferred tasks 4.1–4.3. The deterministic alert path remains intact; a declared future reasoning consumer must use the adopted admission, authority, exposure, causal-chain and budget contracts rather than treating the security label as permission to act.
