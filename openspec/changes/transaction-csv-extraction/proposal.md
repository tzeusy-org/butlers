## Why

The finance butler currently ingests transactions one-at-a-time from email notifications. Users with months or years of credit card and bank transaction history in CSV exports have no way to bulk-import that data. CSV formats vary wildly across institutions and even change over time from the same source, so a rigid column-mapping approach is brittle. We need an adaptive, LLM-driven import that handles arbitrary CSV layouts without human validation loops or loading entire files into the LLM's context window.

## What Changes

- **New bulk transaction ingestion endpoint** — accepts an array of normalized transaction objects, returns per-row success/skip/error counts. Deduplicates on a composite key (posted_at + amount + merchant + account_id) since CSV rows have no `source_message_id`.
- **New `bulk_record_transactions` MCP tool** — exposes bulk ingestion to butler runtime sessions, wrapping the same logic as the API endpoint.
- **New `transaction-csv-extraction` skill** — guides the LLM through an adaptive CSV import workflow: sample the file header + first rows, write a throwaway Python script that parses the full CSV and POSTs normalized records to the bulk endpoint, execute the script, handle errors by fixing and re-running, report results.
- **Composite dedup key for CSV imports** — extends the existing `source_message_id`-based dedup with a fallback composite key so re-importing the same CSV is idempotent.

## Capabilities

### New Capabilities
- `bulk-transaction-ingestion`: Bulk endpoint and MCP tool for importing normalized transaction arrays with composite deduplication
- `csv-extraction-skill`: LLM-driven adaptive CSV parsing skill — samples file, generates parser script, executes it, self-corrects on failure

### Modified Capabilities
- `butler-finance`: Tool inventory gains `bulk_record_transactions`; dedup strategy extends to composite keys for non-email sources

## Impact

- **Database**: New partial unique index on `(posted_at, amount, merchant, account_id)` for composite dedup on the facts layer (metadata JSONB fields)
- **API**: New `POST /api/finance/transactions/bulk` endpoint in `roster/finance/api/router.py`
- **MCP tools**: New `bulk_record_transactions` tool in `roster/finance/tools/`
- **Skills**: New `transaction-csv-extraction` skill in `roster/finance/.agents/skills/`
- **Dependencies**: No new external dependencies — uses stdlib `csv` module in generated scripts and existing `httpx`/`aiohttp` for POSTing
