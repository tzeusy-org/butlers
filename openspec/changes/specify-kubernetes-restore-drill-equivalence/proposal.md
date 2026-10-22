## Why

The Helm chart excludes the restore-drill executor because its current trust boundary is
Docker-specific. `bu-viat6h.4` explicitly requests a DESIGN-FIRST proposal, not a port.
Pod UID and ordinary NetworkPolicy do not reproduce the root-prepared generation,
boot, host-route and before-both-start/pre-secret firewall capability. This proposal makes those gaps
reviewable while retaining the already-adopted isolated credential and result authority.

**Status: PROPOSED, NOT ADOPTED.** Native SHALL/MUST statements below describe a
candidate only. No Kubernetes equivalent, weakening, privilege, credential placement,
implementation, operational action or production topology is accepted by publication.
The current executor remains chart-excluded and Compose-only via `--with-restore-drill`.

## What Changes

- Add a distinct proposed `kubernetes-restore-drill` capability with generation,
  enforcement, credential/result, relay/TLS, scratch, concurrency, truth, rollback
  and exact-adoption scenarios, without modifying existing requirements.
- Describe three concrete dispositions: retain Compose; an unimplemented stronger
  node-fenced protocol with explicit new-authority gates; or a normal Kubernetes
  design with individually enumerated unadopted weakenings.
- Bind every source requirement/scenario/clause and current caller to exact candidate
  scenarios or its unchanged foreign requirement/scenario/actual owner, explicitly
  recording inherited contradictions, using `obligations-ledger.json` and `source-manifest.json`.
- Separate exact source review and adoption from implementation and actual
  PostgreSQL, enforcement, secret provisioning and deployment evidence.

## Capabilities

### New Capabilities

- `kubernetes-restore-drill`: candidate Kubernetes restore-drill equivalence and
  explicit weakening/adoption gates.

### Modified Capabilities

None. REQ-deployment-hardening-007, REQ-database-security-006 and current protected
restore-result/bootstrap contracts remain intact. Backup recovery, API/attention,
Fleet restore and tracker requirements keep their existing owners and approval state.

## Impact

Only this new OpenSpec change is authored. No product code, migration, chart,
NetworkPolicy, role/RBAC, privilege, host, secret, restore or deploy behavior changes.
No migration or numbered RFC is reserved. Tests: +0 ~0 -0.

Source baseline is main/origin `2917096b5e7f3f46b26f7f4b27aadb6980fd1922`.
The root checkout is older `b747a55a530364d987fb37457c343193e9362fe7`; relevant
restore sources match. Source proves neither installed CNI nor deployed production.
Production remains UNKNOWN; `bu-u03nw8` owns that reconciliation. `bu-r6wzr`
Option C excludes even production runbook preparation and verification.

See [design](design.md), [original obligations](original-obligations.md),
[verification](verification.md), [coverage](obligations-ledger.json), and [status](status.md).
