# Dashboard Decisions

## Purpose

Owner decision bu-g2zuv2 = A (2026-10-21) removes engineering tracker tooling
from the personal-assistant product. This first slice retires frontend entry
points and the Helm bridge. Backend endpoints, jobs and stored intent history
remain pending later removal and a deployment-safe migration.

## Requirements

### Requirement: Engineering Tracker Product Entry Points Retired

ID: REQ-dashboard-decisions-002
Source: owner decision bu-g2zuv2 = A; bu-99t5s3.6 steps 1-2
Scope: Dashboard navigation, routing, data queries and Helm bridge retirement

The dashboard SHALL NOT expose Decision Desk or bead-detail pages, navigation,
palette entries, chords, prefetches or digest badge queries. Engineering
choices remain in `bd` or an owner session. The Helm chart SHALL NOT run the
tracker bridge or mount its export into runtime pods. Removing the bridge
MUST preserve the existing effective runtime-to-tracker network confinement.
Approvals, their callbacks, QA and owner authentication SHALL retain their
existing product behavior and authorization boundaries.

#### Scenario: Retired destinations are absent

- **WHEN** the dashboard route and command registries are built
- **THEN** `/decisions` and `/beads/:beadId` are absent
- **AND** no decision digest badge query or prefetch runs from the shared shell
- **AND** Approvals and QA retain their existing destinations and badges

#### Scenario: Bridge retirement preserves tracker confinement

- **WHEN** the Helm chart renders with its existing dev tracker deny enabled
- **THEN** no tracker bridge workload, export PVC, tracker credential sync or export mount is rendered
- **AND** all runtime pods remain denied the configured tracker IPv4 addresses
- **AND** missing tracker CIDRs refuse rendering rather than relax isolation

#### Scenario: Partial retirement does not claim data removal

- **WHEN** only this first retirement slice has landed
- **THEN** backend/API and historical data retirement remain pending
- **AND** no table drop or change to the engineering decision-bead convention is implied
