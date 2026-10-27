## Why

The current k3s deployment runs database clients that the unimplemented
[Compose cutover proposal](../authorize-relationship-effective-time-cutover/proposal.md)
cannot inventory or fence. A successful Helm rollout, a healthy receiver, a boot
ledger, or a suspended Deployment does not prove that every old Relationship
writer is absent. The legacy index must remain until the complete adopted
mutator and cutover proof succeeds.

This is a source-only extension proposal for `bu-0kf2fd`, inspected at
`77722227d9e9481c282b97dd66a58961a05927dc`. It supplies no executable cutover,
signer, credential, cluster permission, migration or deployment.

## What Changes

- Propose one Kubernetes-specific extension to the existing
  `relationship-effective-time-cutover` capability. The eight existing
  requirements, their 52 scenarios, Compose proposal/design/tasks and operator
  packet remain verbatim. The extension neither replaces their Compose v1 wire
  format nor pretends their fence works on Kubernetes.
- Inventory the actual credentialed workloads, controller and initContainer
  seams, host entrypoints, restore paths and unsupported starts in
  [source-inventory.md](source-inventory.md).
- Specify a complete immutable target and actor binding, persistent lifecycle
  admission fence, pre-DDL database exclusion, interrupted/concurrent recovery,
  and exact target release in [design.md](design.md).
- Present concrete signer and test-isolation custody alternatives for later
  adoption, with no selection or provisioning here.
- Allocate cohesive future source/tests and operational dependencies in
  [verification-and-allocation.md](verification-and-allocation.md). Runtime
  coverage and test deltas are future measured obligations, not current proof.

## Capabilities

### New Capabilities

None. Kubernetes is an extension of the existing cutover guarantee.

### Modified Capabilities

- `relationship-effective-time-cutover`: add a proposed Kubernetes topology
  requirement while retaining every existing Compose and unsigned admission
  obligation. The capability currently lives in the unarchived parent proposal;
  complete parent-plus-extension generation must precede any later adoption.

## Impact and authority

Only new proposal documents are authored. Runtime, chart, launcher, migrations,
baseline specs, existing proposal files and tests remain unchanged.
Tests: +0 ~0 -0.

`bu-h3b7t` remains the whole-feature implementation owner; `bu-2z6jyb` retains
its complete unsigned fresh/disposable and interrupted/bootstrap/committed
resume outcome; `bu-jnnxtq` remains allocated to the same isolated test runtime.
All are unimplemented at this inspected epoch. CLOSED `bu-ftd491` answer A and
CLOSED `bu-ldcp5f` are consumed once, not reopened.

Proposal review and merge do not adopt the changed Kubernetes proof/custody
contract, transfer an existing source hunk, authorize implementation outside a
later exact allocation, select a signing key, or authorize a live act. Later
contract adoption, independently reviewed source delivery and exact environment
operations are distinct gates. No live dev or production observation was made.
