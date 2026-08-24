## ADDED Requirements

### Requirement: Secrets Inventory Availability and Bounded Source Reads

`GET /api/secrets/inventory` SHALL complete its source reads within a
server-side deadline below the dashboard client's request timeout. It SHALL
retain rows only from sources whose credential and audit evidence completed
within that source's budget. The endpoint SHALL preserve the existing
content-blind inventory field contract for every returned row.

#### Scenario: Complete inventory remains unchanged

- **WHEN** every configured butler source and the shared credential source
  complete within their source budgets
- **THEN** the response retains the existing inventory fields and omits
  `meta.sources_degraded`

#### Scenario: A slow butler source produces an honest partial inventory

- **WHEN** a configured butler source exceeds its source-read budget
- **THEN** that source contributes no rows, its stable butler name appears in
  `meta.sources_degraded`, and counts are computed only from returned rows
- **AND** the response contains no database error text, credential value,
  probe message, audit note, raw scope, persisted user type, or persisted user
  label

#### Scenario: A slow shared source produces an honest partial inventory

- **WHEN** the shared system, user, CLI, or identity source bundle exceeds its
  source-read budget
- **THEN** that bundle contributes no rows or identities and
  `meta.sources_degraded` contains `shared-public`
- **AND** completed butler-source rows remain available

#### Scenario: Unavailable audit evidence is not presented as empty history

- **WHEN** a source cannot complete its credential audit-evidence read within
  its source-read budget
- **THEN** that source contributes no rows and is named in
  `meta.sources_degraded`
- **AND** the endpoint SHALL NOT publish `audit: []` as a truthful empty
  history for that omitted source

#### Scenario: A partial zero inventory is visibly incomplete

- **WHEN** `meta.sources_degraded` is non-empty and the returned inventory has
  zero failing credentials
- **THEN** the passport names the unavailable sources and does not assert that
  every credential is accounted for
