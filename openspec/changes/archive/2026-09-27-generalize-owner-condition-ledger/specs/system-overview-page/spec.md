## ADDED Requirements

### Requirement: Standing Conditions Panel Spans Both Ledgers

`GET /api/system/conditions` SHALL accept an optional `ledger` query
parameter (`infra`, the default, or `owner`), reading `public.
infra_conditions` or `public.owner_conditions` respectively through the
matching facade, and returning the same `ConditionsFacts` envelope shape
with each `ConditionEntry` tagged with its `ledger`. The System page's
existing Standing Conditions panel SHALL query both ledgers and render them
merged into one most-recently-detected-first list, rather than a second
duplicate panel.

#### Scenario: Omitting ledger preserves existing infra behavior

- **WHEN** `GET /api/system/conditions` is called without a `ledger`
  parameter
- **THEN** it behaves exactly as the existing "Standing Infrastructure
  Conditions" requirement describes, reading `public.infra_conditions`, with
  each returned `ConditionEntry` additionally carrying `ledger: "infra"`

#### Scenario: ledger=owner reads the owner condition ledger

- **WHEN** `GET /api/system/conditions?ledger=owner` is called
- **THEN** the response reads `public.owner_conditions` via `butlers.core.
  owner_conditions.list_conditions`, with each `ConditionEntry` carrying
  `ledger: "owner"`
- **AND** an invalid `ledger` value returns HTTP 400

#### Scenario: Panel merges both ledgers into one list

- **WHEN** the System page renders the Standing Conditions panel
- **THEN** it queries both `ledger=infra` (default) and `ledger=owner`,
  merges the results deduplicated by `id` and sorted by `first_detected_at`
  descending, and labels each row with a small ledger badge

#### Scenario: One ledger degraded does not hide the other

- **WHEN** one ledger's query reports `conditions_available: false` (or
  errors) while the other succeeds
- **THEN** the panel still renders the available ledger's rows plus a named
  degraded note for the unavailable one, rather than treating the whole
  panel as degraded or silently omitting the failed ledger

#### Scenario: QA-dispatch suppression counts apply only to infra rows

- **WHEN** the panel renders a merged list containing an owner-ledger row
- **THEN** that row never computes or displays a QA-dispatch suppression
  count (owner conditions have no QA-dispatch suppression concept), while
  infra-ledger rows retain the existing suppression-count behavior
