# Fold the historical-data-import skill into transaction-csv-extraction

## Why

After bu-0xvf2c moved `transaction-csv-extraction` onto `import_transactions(storage_ref)`, the
finance `historical-data-import` skill was the last finance skill describing a different import
path. It was a stub: pseudocode `parse_csv(file)` format detection, an offer to auto-discover a
folder of local statements, a separate deduplication pass, and a separate baseline step. None of
that matches the runtime. `import_transactions` already detects the format, deduplicates against
the ledger, and recomputes baselines on every call, and no finance tool accepts a local path
(`finance-data-import`). A historical backfill is the single-file flow repeated once per stored
statement, so a second skill would only duplicate the token-budget and no-local-path rules and
drift from them (bu-aljhcv).

## What Changes

- `CSV extraction skill definition` gains a historical-backfill scenario: one `storage_ref` per
  statement, no folder discovery, dry-run every statement before importing any, import each clean
  statement with its own call, hold back failed ones, and report per-statement counts plus totals.
- `Finance Butler Skills` drops `historical-data-import` from the inventory and says
  `transaction-csv-extraction` also covers a multi-statement backfill.
- `Finance Butler Intelligence Behavioral Guidelines` points the insufficient-data suggestion at
  `transaction-csv-extraction`.

## Impact

- `roster/finance/.agents/skills/historical-data-import/` is deleted; its backfill guidance lives
  in a new section of `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md`.
- `roster/finance/AGENTS.md` skill list and insufficient-data guidance are updated.
- The pending `generalist-lead-focused-delegation` deltas for `butler-finance` and
  `csv-extraction-skill` are aligned so archiving them later does not reintroduce the retired skill
  or drop the backfill scenario. Its `Finance Butler Skills` block now carries
  `ID: REQ-butler-finance-003`, because editing it moves it out of the trace-line baseline.
