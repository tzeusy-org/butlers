## ADDED Requirements

### Requirement: Interim Development Tracker Bridge Is Confined to One Management Workload

By owner ruling of 2026-10-08 (`bu-sng0tu`, superseding its 2026-10-04 "wait for the RFC 0025
exporter" disposition), the `butlers-dev` deployment MAY place tracker reachability and a tracker
credential in exactly one management workload: the beads export CronJob, which applies recorded
Owner Decision Desk intents and then refreshes `issues.export.jsonl`. This requirement amends the
"Runtime has no tracker capability" boundary of `REQ-beads-projection-001` for that workload only;
every other clause of `REQ-beads-projection-001` continues to bind, and the RFC 0025 projection
remains the selected long-term reader. The bridge is an explicit deployment selection, never an
automatic fallback for the projection.

The management workload SHALL run a dedicated image whose `bd` binary is pinned to an exact
release version and release-artifact SHA-256 that the build verifies. The image SHALL contain no
Butlers runtime package code beyond the applier and export scripts. The workload SHALL authenticate
to the tracker with a dedicated least-privilege tracker user scoped to the tracker's `butlers`
database, delivered through a Secret that only this workload mounts. The credential SHALL come
from the deployment's secret manager by explicit key name and SHALL never be committed, rendered
into chart values, or written to logs. The tracker host SHALL remain site-specific configuration.

`dashboard-api`, `butlers-up`, connectors, and every other pod in the namespace SHALL receive no
tracker host, port, credential, `bd` binary, or `.beads` workspace. They SHALL keep only the
existing read-only export-file mount. When the bridge is enabled, the deployment SHALL also apply a
network policy that denies every non-bridge pod in the namespace egress to the tracker address,
while preserving their other egress, so tracker reachability is enforced rather than conventional.
Disabling the bridge SHALL remove the workload, its Secret consumer, and its tracker egress
allowance together.

ID: REQ-beads-projection-007
Source: RFC 0025 §§1-2 (amended by owner ruling 2026-10-08); bu-sng0tu; bu-ckkpz.3
Scope: v1-mandatory

#### Scenario: Only the bridge workload holds tracker capability

- **WHEN** the chart renders with the development bridge enabled
- **THEN** only the beads export CronJob's pod carries `BEADS_DOLT_*` environment, the tracker
  credential Secret reference, and the pinned `bd` image
- **AND** `dashboard-api` and `butlers-up` carry only the read-only export directory mount

#### Scenario: Runtime pods cannot reach the tracker

- **WHEN** any pod in the namespace other than the bridge workload opens a connection to the
  tracker address
- **THEN** the namespace network policy refuses that egress
- **AND** the same pod's egress to the database, the Telegram API, and in-cluster services is
  unchanged

#### Scenario: Credential stays out of tracked files and logs

- **WHEN** the bridge is configured for a deployment
- **THEN** chart values name only the secret-manager keys and the Secret name
- **AND** neither the rendered manifests, the image, nor the workload's logs contain the tracker
  password

#### Scenario: An unpinned or mismatched bd build fails

- **WHEN** the bridge image build downloads a `bd` release whose SHA-256 differs from the pinned
  value
- **THEN** the build fails before producing an image
