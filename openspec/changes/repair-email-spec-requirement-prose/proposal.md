## Why

`openspec archive` revalidates each **rebuilt** spec in full, so a pre-existing
defect anywhere in a baseline blocks every change that touches that spec -- even
one that never goes near the broken requirement. Four requirements are defective
this way, and together they are the only remaining blockers on
`true-bidirectional-email-correspondence`:

- `connector-gmail`: `ingest.v1 Field Mapping`, `Aggregated Health Status`, and
  `Environment Variables` carry scenarios but no requirement prose at all, so
  they hard-fail `must contain SHALL or MUST`.
- `module-email`: `Classification Pipeline Integration (Removed)` has prose
  without an RFC-2119 keyword and no scenario.

`openspec validate --strict` is blind to this class -- it checks change deltas,
not the rebuilt baseline -- so the defect stays invisible until an unrelated
change tries to archive and aborts.

## What Changes

- Add RFC-2119 requirement prose to the three scenario-only `connector-gmail`
  requirements, stating the obligation their existing scenarios already
  describe.
- Give `module-email`'s `Classification Pipeline Integration (Removed)` an
  RFC-2119 keyword and the one scenario it has always implied: the removed tool
  is not registered, and ingestion arrives via the connector pipeline.
- Nothing else. Each `## MODIFIED` block reproduces every scenario the baseline
  carries, verbatim; no scenario is added beyond the single one required to make
  a scenario-less requirement valid, and no behavior is redefined.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-gmail`: three requirements gain the normative prose their scenarios
  already assumed.
- `module-email`: the removed-tool requirement gains an RFC-2119 keyword and a
  scenario asserting the removal.

## Impact

- No production code, database, API, or test changes. This change repairs spec
  text only.
- The repair is its own change on purpose. Bolting `## MODIFIED` blocks for
  these four onto whichever proposal happened to trip over them would hide a
  baseline repair inside an unrelated review.
- Unblocks `true-bidirectional-email-correspondence`, which must be archived
  **after** this change.
- The rewrite is deliberately conservative: a repair that restates is safe to
  archive without re-reviewing the connector's behavior, whereas a repair that
  sharpens wording would silently become the contract. Anything that reads as
  under-specified in these four requirements stays under-specified here and
  belongs in its own change.
