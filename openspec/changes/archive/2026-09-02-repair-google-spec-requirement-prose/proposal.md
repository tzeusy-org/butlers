## Why

`openspec archive` revalidates each **rebuilt** spec in full, so a defective
requirement hard-fails and aborts every change that touches that spec —
including a change that never goes near the broken requirement. Eight Google
specs are unarchivable for this reason, carrying 32 defects between them:
`module-google-health` (6), `connector-google-health` (6),
`google-multi-account-oauth` (5), `module-google-drive` (4),
`connector-google-calendar` (3), `connector-google-drive` (3),
`google-account-registry` (3), and `dashboard-google-accounts` (2).

Thirty-one have no requirement prose at all and hard-fail `must contain SHALL or
MUST`. One — `connector-google-health` / `Structural Cost Gates Not Applicable` —
has prose but no scenario, and hard-fails `must have at least one scenario`.

Confirmed by archiving a verbatim no-op `## MODIFIED` block against each spec in
a scratch copy: all eight abort, with error counts matching the defect counts
exactly.

## What Changes

- Add one sentence of RFC-2119 requirement prose to each of the 31 prose-less
  requirements, stating the obligation its existing scenarios already describe.
- Add one scenario to `Structural Cost Gates Not Applicable`, restating the
  `SHALL NOT` its prose already carries.
- Nothing else. Every `## MODIFIED` block reproduces the requirement's scenarios
  verbatim; no other scenario is added, dropped, or reworded.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-google-calendar`, `connector-google-drive`: the ingest mapping,
  health, and environment requirements gain normative prose.
- `connector-google-health`: five polling and filtering requirements gain prose;
  the structural-cost-gate requirement gains a scenario.
- `google-multi-account-oauth`, `google-account-registry`,
  `dashboard-google-accounts`: OAuth, registry, and status requirements gain
  prose.
- `module-google-drive`, `module-google-health`: module identity, tool, and
  query requirements gain prose.

## Impact

- No production code, database, API, or test changes. Spec text only.
- Makes all eight specs archivable by any future change that touches them.
- The prose deliberately restates rather than sharpens. A restatement can be
  archived without re-reviewing the connectors' and modules' behavior; a
  sharpening would silently become the contract.
- The three connectors share the same three defective requirement names
  (`ingest.v1 Field Mapping`, `Aggregated Health Status`, `Environment
  Variables`) as `connector-gmail`, because the specs were written from a common
  template. The prose is adapted per connector rather than copied: Google
  Calendar has no per-account override scenario, so its environment prose omits
  the override clause the Gmail and Drive versions carry.
