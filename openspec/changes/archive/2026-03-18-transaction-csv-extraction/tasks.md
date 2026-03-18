## 1. Bulk Ingestion Endpoint

- [ ] 1.1 Add Pydantic models for bulk request/response to `roster/finance/api/models.py`: `BulkTransactionItem`, `BulkTransactionRequest` (with `transactions`, `account_id`, `source` fields), `BulkTransactionResponse` (with `total`, `imported`, `skipped`, `errors`, `error_details` where each detail has `index` and `reason`)
- [ ] 1.2 Implement `POST /api/finance/transactions/bulk` in `roster/finance/api/router.py`: validate batch size (max 500), iterate rows, call fact-layer with composite dedup key, collect per-row results, return `BulkTransactionResponse`
- [ ] 1.3 Implement composite dedup key computation with **canonicalization**: posted_at → UTC ISO 8601 Z-suffix second precision, amount → `str(Decimal(amount).quantize(Decimal("0.01")))`, merchant → as-is, account_id → lowercased or empty string. Hash: `sha256(f"{canonical_posted_at}|{canonical_amount}|{merchant}|{canonical_account_id}")`
- [ ] 1.4 Implement **embedding bypass** for bulk path: skip `embedding_engine.embed()`, store zero vector or NULL placeholder, still compute `search_vector` (tsvector). Ensure 500-row batch completes within 10 seconds.
- [ ] 1.5 Add `import_source` metadata tagging: when top-level `source` field is present, inject `"import_source": <value>` into each fact's metadata JSONB
- [ ] 1.6 Ensure `error_details` distinguishes skip reasons: `"duplicate"` for dedup skips vs specific validation errors like `"invalid_date"`, `"missing_field"`

## 2. Bulk MCP Tool

- [ ] 2.1 Implement `bulk_record_transactions` function in `roster/finance/tools/facts.py`: accepts `transactions` list, optional `account_id` and `source`, processes via same logic as HTTP endpoint, returns same response shape
- [ ] 2.2 Register `bulk_record_transactions` as an MCP tool in the finance butler's tool registration (ensure it appears in the runtime tool list)

## 3. CSV Extraction Skill

- [ ] 3.1 Create `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md` with frontmatter (`name`, `description`, `version`)
- [ ] 3.2 Write the CRITICAL CONSTRAINTS section at the top of the skill: explicit MUST NOT for full-file context loading and per-row tool calls, with reasons (token budget)
- [ ] 3.3 Write the sampling phase instructions: read header + first 10 rows, infer column mapping, date format, delimiter, amount convention (single vs split debit/credit columns)
- [ ] 3.4 Write the script generation instructions: stdlib-only Python, batch POST to `/api/finance/transactions/bulk`, BOM handling, JSON summary to stdout, `--api-url` override, `BUTLERS_API_URL` env var check, pre-flight connectivity HEAD request
- [ ] 3.5 Write **amount sign normalization** instructions: split debit/credit columns → negate debits; single column with type indicator → negate debits; positive-only credit card statements → negate all charges, keep refunds positive
- [ ] 3.6 Write the execution and self-correction instructions: run script, read stderr on failure, fix and re-run (max 3 attempts), parse JSON summary from stdout on success
- [ ] 3.7 Write the result reporting instructions: report totals to user, warn on high skip rate (>20%), explain re-run semantics (previously imported rows appear as "skipped (duplicate)")
- [ ] 3.8 Add worked examples: at least two complete examples showing the skill flow end-to-end (one simple CSV with standard columns, one with split debit/credit columns and non-standard date format)

## 4. Spec Updates

- [ ] 4.1 Update `roster/finance/tools/tool-reference` skill SKILL.md with `bulk_record_transactions` parameter documentation
- [ ] 4.2 Update `roster/finance/AGENTS.md` tool list to include `bulk_record_transactions` and the `transaction-csv-extraction` skill

## 5. Tests

- [ ] 5.1 Write unit tests for the bulk endpoint: successful import, dedup (same rows imported twice → second batch all skipped with reason "duplicate"), per-row error handling (mix of valid and invalid rows), batch size limit (>500 → 422), account_id inheritance, source metadata tagging
- [ ] 5.2 Write unit tests for composite dedup key canonicalization: different ISO 8601 representations of same timestamp produce same key, different decimal representations of same amount produce same key, different transactions produce different keys
- [ ] 5.3 Write unit tests for embedding bypass: bulk-ingested facts have NULL/zero embedding, tsvector still computed, idempotency dedup still works without real embedding
- [ ] 5.4 Write unit tests for `bulk_record_transactions` MCP tool: same scenarios as endpoint tests, verify response shape matches
- [ ] 5.5 Write performance test: 500-row batch completes within 10 seconds (regression guard for embedding bypass)
