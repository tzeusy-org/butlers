# Align the CSV extraction skill with storage_ref-only import

## Why

PR #5341 (bu-o3bt53) removed the finance `import_transactions_from_file` tool:
`import_transactions(storage_ref)` is now the only way to name CSV bytes for import, and
`finance-data-import` says no import tool accepts a local filesystem path. The
`csv-extraction-skill` capability still required the opposite: sample a caller-given local file,
generate a Python script that reads it from disk, and POST rows to the dashboard bulk endpoint.
That kept a local-path read in the finance runtime's own shell after the tool was gone (bu-0xvf2c).

## What Changes

- `CSV extraction skill definition` now takes a `storage_ref`, asks for an upload when the user
  offers only a local path, and drives a dry-run preview then one import through
  `import_transactions(storage_ref)`, with an explicit `column_map` when detection fails and a stop
  when no column map can fix the sign convention. Result-reporting and re-run guidance move here.
- `Token budget protection` limits CSV content in context to the dry-run `preview` and
  `error_details`, and names `import_transactions(storage_ref)` as the only ingestion path.
- Remove `Script-generation workflow`, `API connectivity verification`, and
  `Dashboard API URL discovery`.

## Impact

- `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md` is rewritten to this flow.
- The pending `generalist-lead-focused-delegation` delta for this capability is aligned to the
  storage_ref inputs so archiving it later does not reintroduce a file path.
