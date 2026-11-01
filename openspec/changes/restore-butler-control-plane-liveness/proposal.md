## Why

The 2026-09-23 incident showed owner authentication rejecting the daemon's unauthenticated heartbeat POST. The resulting registry expiry blocked routing and also suppressed QA's scheduled patrol, while process health remained green. The 2026-09-23 [focused reliability review](../../../docs/plans/2026-09-23-liveness-control-plane-reliability-packet.md) confirms this composition failure and the unreliable quarantine semantics. Butlers' adopted vision requires continuously reliable service availability and scheduled work. RFC 0038 now governs the target: domains are capability services, reasoning belongs to goal/invocation execution on the session host, and service eligibility is independent of that reasoning activity. This amendment applies the existing [generalist collision disposition](../generalist-lead-focused-delegation/collision-dispositions.md) before either change archives; it changes no RFC decision or runtime activation gate.

## What Changes

- Replace caller-asserted daemon heartbeat writes with a supervised, receiver-driven observer of exact Git-roster daemon endpoints. Allocate a durable monotonic boot epoch at each daemon registration, verify daemon name, configured endpoint, UUID and current epoch, service compatibility contract, and service acceptance readiness; use DB-server observation time.
- Separate observed health, administrative pause/quarantine, and service compatibility. Migrate existing quarantine by provenance before cutover. Health probes, startup registration, and authorized service-call success never clear administrative quarantine.
- Let Switchboard make its own bounded stale-target probe through the same roster verifier and narrowly authorized DB sequence/CAS operations used by Dashboard's periodic observer; return a typed no-attempt outcome when the target remains unavailable.
- Keep deterministic local schedules and QA patrol running when only remote registry observation is stale. Independently supervise fleet reconciliation and QA patrol age using only complete successful all-source patrols; correlate expiry into one durable condition with per-butler impact and retire redundant per-butler pages without falsely resolving old evidence.
- Create one durable content-blind owner-attention episode per fleet-control or overdue-QA condition through the existing fenced Switchboard outbox, independently of QA patrol. Preserve bounded condition escalation and truthful pending, sent, failed, and uncertain delivery status.
- Preserve lightweight process `/health`. Give L3 one read-only Switchboard
  service-preflight producer (retaining the internal route-preflight compatibility path) and Q4 the sole public `/ready` plus owner-auth
  exception; supported deployment consumers use that route under the actually adopted deployment contract, not a revived production Compose mode. Make deployment completion depend on
  distinct qualifying observer/patrol cycles and an effect-free canary beyond
  a full liveness TTL.
- **BREAKING:** Retire `POST /api/switchboard/heartbeat` as liveness authority and the daemon heartbeat reporter after observer cutover. Do not create an anonymous auth exemption or a new process-bound signing key.

The separate ingestion-to-domain delivery recovery changeset owns durable per-target intents, acceptance identity, and historical recovery. The existing separate-host minimal process-health monitor remains a distinct owner operation. Publishing semantic readiness to an external monitor or provisioning a new monitor requires a further owner decision. Split-host trust changes and WhatsApp classification are outside this change.

## Capabilities

### New Capabilities

- `butler-control-plane-liveness`: Receiver-derived observations, generation fencing, policy/compatibility separation, migration, on-demand recovery, fleet condition identity, and durable owner attention.

This capability makes the control plane the authority for the expected fleet's observed health while preserving owner policy and correlated failure evidence.

### Modified Capabilities

- `butler-base-spec`: Replace retired heartbeat tasks across daemon lifecycle, domain registration, and instance facts with durable boot registration and bounded internal identity/readiness facts.
- `butler-switchboard`: Derive service/capability eligibility from observed health,
  administrative policy, and compatibility while preserving staffer
  reachability; expose only a bounded read-only internal preflight to Q4.
- `staffer-qa`: Keep local patrol independent of derived registry staleness, make patrol-age detection require successful complete-source cycles, and use fleet-correlated findings. Reconcile the active `durable-dashboard-terminal-action-recovery` V1 discovery-source block in place.
- `dashboard-api`: Strengthen canonical `/ready` and distinguish semantic readiness from process liveness.
- `system-overview-page`: Identify receiver-derived service facts separately from goal/invocation activity; retain historical session projections without implying domain reasoning-health.
- `dashboard-butler-management`: Preserve the current status-board compatibility and failure/accessibility/policy guarantees until the actual switch; allocate region operator views and goal activity presentation to the separately signed D19 change.

## Impact

Daemon health and scheduler, boot registration, dashboard lifespan supervisor and readiness API, Switchboard service eligibility/registry/migrations and runtime-attention worker, QA condition producer and dashboard projections, supported launcher and effect-free service preflight, tests, topology and runbooks. The runtime-attention outbox currently accepts only `model_breaker` and `fleet_halt`; its source/edge-key and safe-payload constraints, fixed producer grant, and worker formatter require an additive migration before fleet/QA attention can deliver. RFC 0038 governs this amendment; alignment of RFCs 0001, 0003, 0006, 0007, 0008, and 0015 belongs to the existing post-switch corpus/terminal work, not edits in this child. The active `define-infrastructure-reliability-lifecycle` change excludes generic heartbeat and scheduler work; this change is a companion contract and preserves its complete-snapshot and bounded-escalation rules.

The QA manifesto and canonical QA investigation retention requirement are
corrected against their existing nested cleanup implementation. The exact
`Raw Log Retention and Cleanup` requirement has no other active `MODIFIED`
block; this clarification changes no runtime retention job.

## Re-scope and delivery boundary

All 21 requirements and 98 scenario headings remain accounted for. The three shared `butler-base-spec` blocks retain the receiver/boot-policy guarantees together with generalist's domain-service, broker assertion and goal/invocation semantics. Their archive owner must rebuild the non-lossy union against then-current baseline; neither whole-block overwrite is sufficient. Existing mechanism-named headings remain, with their applicability stated in the body.

Current Butler List and session DTOs are retained current-runtime/historical contracts, not specifications for a new region dashboard. D19 owns the signed region operator experience and the actual replacement of views. This child does not add activity fields, reason about source access by inventing SQL permissions, or infer healthy zero activity from unavailable goal data.

Checked historical tasks remain dated implementation evidence, not target-runtime verification. Parent runtime delivery, generalist cutover/archive, D19 signoff/build, supervisor ownership, sustained deployment evidence and external-monitor gates remain pending on their existing owners. No code, tests, baseline spec, RFC, guard baseline, deployment or credential changes, archive, unfinished-task ticks or live probes belong to this documentation amendment. Tests: +0 ~0 -0.
