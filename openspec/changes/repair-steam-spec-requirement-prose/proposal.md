## Why

`openspec archive` revalidates each **rebuilt** spec in full, so a requirement
with no prose at all hard-fails `must contain SHALL or MUST` and aborts every
change that touches that spec — including a change that never goes near the
broken requirement. Three Steam specs are unarchivable for this reason:
`module-steam` (10 requirements), `dashboard-steam` (3), and
`steam-account-registry` (1).

Confirmed by archiving a verbatim no-op `## MODIFIED` block against each spec in
a scratch copy: all three abort, with error counts matching the defect counts
exactly. This is the same defect class `repair-email-spec-requirement-prose`
fixed in `connector-gmail` and `module-email`.

## What Changes

- Add one sentence of RFC-2119 requirement prose to each of the 14 prose-less
  requirements, stating the obligation its existing scenarios already describe.
- Nothing else. Every `## MODIFIED` block reproduces the requirement's scenarios
  verbatim; no scenario is added, dropped, or reworded.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-steam`: ten tool requirements gain the normative prose their scenarios
  already assumed.
- `dashboard-steam`: three endpoint and UI requirements gain the same.
- `steam-account-registry`: the account lifecycle requirement gains the same.

## Impact

- No production code, database, API, or test changes. Spec text only.
- Makes all three specs archivable by any future change that touches them.
- The prose deliberately restates rather than sharpens. A restatement can be
  archived without re-reviewing the Steam module's behavior; a sharpening would
  silently become the contract. Where a requirement reads as thin, it stays thin
  here and belongs in its own change.
