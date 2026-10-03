## Why

New-sign-in, password-changed, 2-step-changed and recovery-changed alerts from identity providers are cut to subject-only by the `noreply` rule, and a promoted rule can turn that into a skip. The owner keeps scanning the inbox for "was this you?" mail. The fleet should perceive these alerts itself without ever touching the codes and links that bearer-material quarantine removes.

## What Changes

- A deterministic classifier (`butlers.account_security`) types first-party provider alerts from sender, subject and, when kept, `Authentication-Results`. No LLM, no body, no bearer material.
- The Switchboard ingest tool publishes one `switchboard.security_event` per alert, independent of the policy decision.
- The global-scope policy evaluator demotes a `skip` on a classified alert to `metadata_only`, and rule promotion refuses to propose `skip`/`metadata_only` for the allowlisted senders.
- An answer door records the owner's yes/no per event id; `no` opens one fleet case carrying the provider's static recovery door.

## Capabilities

### New Capabilities

- `account-security-perception`: classification, trust assumption, event, answer door.

### Modified Capabilities

- `ingestion-policy`: account-security carve-out for skip rules and promotion.
- `domain-event-bus`: the `switchboard.security_event` contract.

## Owner decisions (not applied here)

- No manifesto owns account-security perception. Proposed: a General amendment ("steward of ordinary digital life") or a new Steward specialist.
- Event type is `switchboard.security_event`, not the packet's `estate.security_event`: the contract registry requires the namespace to equal the publishing butler's roster directory and no `estate` butler exists.
