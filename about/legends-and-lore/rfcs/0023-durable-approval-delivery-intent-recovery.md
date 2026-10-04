# RFC 0023: Durable Approval Delivery Intent Recovery

**Status:** Accepted (owner sign-off 2026-09-12; rollout separately gated)
**Date:** 2026-08-13
**Related:** RFC 0006 (schema isolation), RFC 0017 (owner-routing safety),
RFC 0019 (parked automation), RFC 0021 (one-tap approvals), RFC 0022
(cross-process event transport), `about/heart-and-soul/security.md`
(Approval Gates)

## Decision

Use a schema-local transactional delivery intent, fenced presentation
recovery, and a narrow Messenger handoff ledger to recover owner attention for
parked approval actions. Parking and enabled intent admission commit together;
the deterministic recovery worker may recover notification delivery but has no
authority to decide, expire, defer, execute, edit, or otherwise mutate the
parked domain action. Authenticated dashboard defer is the explicit
re-presentation exception; cohort-owned digests preserve burst policy without
making the fourth action their lifecycle authority.

The accepted behavioral contract lives in
[`approval-delivery-intent-recovery`](../../../openspec/specs/approval-delivery-intent-recovery/spec.md).
It owns atomic admission and identity, closed safe state/reason vocabulary,
notification-only authority, leased recovery and timing, independently trusted
source/presentation handoff, provider uncertainty, decision/expiry/defer
fencing, RFC 0021 policy preservation, safe retention/projections, producer
coverage, generic-history isolation, prepared-action silence, and additive
rollout/rollback. Companion module, notify, Messenger, and dashboard specs bind
their integration seams to that protocol. Source canonicalization does not
authorize operational activation; the archived change's canary and activation
tasks remain unchecked. Its [source translation record](../../../openspec/changes/archive/2026-10-05-durable-approval-delivery-intent-recovery/source-translation.md)
records the full original-clause map and observed verification/authority gaps.

## Trade-offs

- **Durability with local ownership.** An atomic intent prevents a lost
  after-commit notification callback from stranding work. Schema-local records
  and MCP handoff preserve isolation without a peer pool, DSN, or grant.
- **Honest uncertainty over duplicates.** The source and Messenger durable
  boundaries cannot make an external provider transactional. A post-start
  timeout stays visibly ambiguous unless the adapter proves a same-key retry
  harmless; confirmed delivery means provider acceptance, not owner attention.
- **Explicit race boundary.** A committed handoff-start marker linearizes
  cancellation. A later decision cannot retract an in-flight network call,
  but fences future recovery; late results remain historical evidence. Defer
  creates an authenticated successor rather than allowing worker rescheduling.
- **Separate authority and history.** Non-secret correlation keys cannot
  authenticate recovery. Independent source attestation and authenticated
  transport precede persistence/egress; protected recovery records avoid
  generic controls and conversation/LLM history. Dormant/default-off rollout
  remains appropriate until the pre-activation authority gap is resolved.
- **Additive operations.** Retaining unresolved evidence favors safety and
  truthful diagnosis over destructive rollback or speculative replay. Canary
  and gradual activation remain separate operational gates.
- **Rejected alternatives.** A later park callback is not guaranteed to run;
  generic deferred notifications lack action/provider fences; bare action
  keys are forgeable correlation data; PostgreSQL NOTIFY is only a wake hint
  (RFC 0022). Holding action locks across provider calls adds contention
  without transactional delivery. Blind resend and worker-driven domain
  transitions cross the chosen safety boundary.
