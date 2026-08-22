# spec_overwrite_regression

A minimal OpenSpec project reproducing **instance 1** of the whole-requirement
overwrite defect (bu-97nlt, bu-s9uv3), used by
`tests/scripts/test_check_spec_overwrites.py`.

## What it is

- `openspec/specs/dashboard-spend-dashboard/spec.md` — the live `Spend API`
  baseline as it stood once `spend-ledger-truth` archived (commit `f1570cf48`),
  trimmed to the ten scenarios the change's block also carries.
- `openspec/changes/make-spend-forecasts-authoritative/…` — that change's
  **pre-rebuild** `Spend API` block, taken verbatim from `f1570cf48^`. It was
  authored against the older ancestor, so it predates everything
  `spend-ledger-truth` contributed.

## Why it is shaped this way

The two sides carry the **same ten scenario names in the same order**. That is
all `findMissingCurrentScenarios` (OpenSpec 1.9.0,
`dist/core/parsers/requirement-blocks.js`) inspects, so
`openspec validate make-spend-forecasts-authoritative --type change --strict`
reports the change as valid.

Archiving it would nonetheless delete twelve baseline clauses, because
`openspec archive` replaces the whole requirement: the ledger-attribution
bullets, the unpriced-model coverage fields, and the UTC calendar default all
live inside scenarios whose *names* both sides agree on.

Trimming the baseline to the shared names is deliberate. Against the untrimmed
baseline the block also drops four scenario names outright, which the name-level
guard does catch — and then the fixture would no longer isolate the blind spot
it exists to demonstrate.

Do not "fix" either file. The divergence is the fixture.
