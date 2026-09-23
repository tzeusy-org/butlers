## Why

Owner authentication now rejects the daemon's unauthenticated heartbeat POST. The resulting registry expiry blocks routing and also suppresses QA's scheduled patrol, while process health remains green. The 2026-09-23 [focused reliability review](../../../docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md) confirms this composition failure and the unreliable quarantine semantics. Butlers' adopted vision requires continuously reliable routing and scheduled work.

## What Changes

- Replace caller-asserted daemon heartbeat writes with a supervised, receiver-driven observer of exact Git-roster daemon endpoints. Allocate a durable monotonic boot epoch at each daemon registration, verify daemon name, configured endpoint, UUID and current epoch, route contract, and acceptance readiness; use DB-server observation time.
- Separate observed health, administrative pause/quarantine, and route compatibility. Migrate existing quarantine by provenance before cutover. Health probes, startup registration, and routing success never clear administrative quarantine.
- Let Switchboard make its own bounded stale-target probe through the same roster verifier and narrowly authorized DB sequence/CAS operations used by Dashboard's periodic observer; return a typed no-attempt outcome when the target remains unavailable.
- Keep deterministic local schedules and QA patrol running when only remote registry observation is stale. Independently supervise fleet reconciliation and QA patrol age using only complete successful all-source patrols; correlate expiry into one durable condition with per-butler impact and retire redundant per-butler pages without falsely resolving old evidence.
- Create one durable content-blind owner-attention episode per fleet-control or overdue-QA condition through the existing fenced Switchboard outbox, independently of QA patrol. Preserve bounded condition escalation and truthful pending, sent, failed, and uncertain delivery status.
- Preserve lightweight process `/health`. Strengthen the canonical `/ready` into content-blind fleet readiness and make deployment completion depend on sustained probe/patrol progress and an effect-free route canary beyond a full liveness TTL.
- **BREAKING:** Retire `POST /api/switchboard/heartbeat` as liveness authority and the daemon heartbeat reporter after observer cutover. Do not create an anonymous auth exemption or a new process-bound signing key.

The separate ingestion-to-domain delivery recovery changeset owns durable per-target intents, acceptance identity, and historical recovery. The existing separate-host minimal process-health monitor remains a distinct owner operation. Publishing semantic readiness to an external monitor or provisioning a new monitor requires a further owner decision. Split-host trust changes and WhatsApp classification are outside this change.

## Capabilities

### New Capabilities

- `butler-control-plane-liveness`: Receiver-derived observations, generation fencing, policy/compatibility separation, migration, on-demand recovery, fleet condition identity, and durable owner attention.

This capability makes the control plane the authority for the expected fleet's observed health while preserving owner policy and correlated failure evidence.

### Modified Capabilities

- `butler-base-spec`: Replace retired heartbeat tasks across daemon lifecycle, domain registration, and instance facts with durable boot registration and bounded internal identity/readiness facts.
- `butler-switchboard`: Derive routing eligibility from observed health, administrative policy, and compatibility while preserving staffer reachability.
- `staffer-qa`: Keep local patrol independent of derived registry staleness, make patrol-age detection require successful complete-source cycles, and use fleet-correlated findings. Reconcile the active `durable-dashboard-terminal-action-recovery` V1 discovery-source block in place.
- `dashboard-api`: Strengthen canonical `/ready` and distinguish semantic readiness from process liveness.
- `system-overview-page`: Identify observer-derived fleet facts without presenting daemon-authored heartbeats as authority.

## Impact

Daemon health and scheduler, boot registration, dashboard lifespan supervisor and readiness API, Switchboard registry/routing/migrations and runtime-attention worker, QA condition producer and dashboard projections, Compose launcher and deployment canary, tests, topology and runbooks. The runtime-attention outbox currently accepts only `model_breaker` and `fleet_halt`; its source/edge-key and safe-payload constraints, fixed producer grant, and worker formatter require an additive migration before fleet/QA attention can deliver. RFCs 0001, 0003, 0006, 0007, 0008, and 0015 require alignment when this contract is adopted. The active `k3s-deployment-helm-chart` change defines the same public `/ready` and is reconciled to the stronger semantic check contract. The active `define-infrastructure-reliability-lifecycle` change excludes generic heartbeat and scheduler work; this change is a companion contract and preserves its complete-snapshot and bounded-escalation rules.
