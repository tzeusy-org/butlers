# Dashboard Decisions

## Purpose

Owner decision bu-g2zuv2 = A (2026-10-21) removes engineering tracker tooling
from the personal-assistant product. Frontend entry points, the Helm bridge and the backend readers and writers
are retired in source. Stored intent history remains until the removal is
actually deployed and a separately reviewed deployment-safe migration runs.

## Requirements

### Requirement: Engineering Tracker Product Entry Points Retired

ID: REQ-dashboard-decisions-002
Source: owner decision bu-g2zuv2 = A; bu-99t5s3.6 steps 1-3
Scope: Dashboard, API, jobs, transports and tracker bridge source retirement

The dashboard SHALL NOT expose Decision Desk or bead-detail pages, navigation,
palette entries, chords, prefetches or digest badge queries. Engineering
choices remain in `bd` or an owner session. The Helm chart SHALL NOT run the
tracker bridge or mount its export into runtime pods. Compose and deployment
helpers SHALL NOT materialize or mount tracker exports. The API SHALL NOT
register decision or bead-detail operations; jobs, schedule configuration and
Telegram transports SHALL NOT read, offer or apply engineering decisions. Removing the bridge
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

#### Scenario: Retired backend and callback destinations have no product authority

- **WHEN** the API registry and Switchboard schedule configuration are built
- **THEN** no engineering decision or bead-detail operation or job is registered
- **AND** approval callbacks and their owner and credential checks remain unchanged
- **AND** stale decision callbacks cannot choose an option or enter ingestion
- **AND** unmatched API request audit bodies omit raw bearer payloads, including malformed and truncated JSON

#### Scenario: Source retirement does not claim data removal

- **WHEN** the source retirement has landed without its deployment
- **THEN** stored intent and prompt tables and migration ancestry remain intact
- **AND** a later drop requires removal to be deployed and all old readers and writers stopped
- **AND** the engineering decision-bead convention and local linter remain available
