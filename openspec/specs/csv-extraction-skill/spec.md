# CSV Extraction Skill

## Purpose
LLM-driven adaptive CSV parsing skill for the finance butler. Guides the runtime through a preview-then-import workflow over `import_transactions(storage_ref)` that handles arbitrary CSV layouts without loading the file into context, reading local paths, or calling per-row tools.

## Requirements

### Requirement: CSV extraction skill definition
The finance butler MUST have a `transaction-csv-extraction` skill in `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md` that guides the LLM through adaptive CSV import.

ID: REQ-csv-extraction-skill-001
Source: finance-data-import "No caller-named local filesystem source"
Scope: v1-mandatory

#### Scenario: Skill discovery
- **WHEN** the finance butler's runtime session activates the `transaction-csv-extraction` skill
- **THEN** the skill MUST be loadable from `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md`
- **AND** the skill MUST have valid frontmatter with `name`, `description`, and `version` fields

#### Scenario: Skill activation trigger
- **WHEN** a user provides a CSV export (as an attachment or dashboard upload) and requests transaction import
- **THEN** the butler MUST activate the `transaction-csv-extraction` skill to guide the workflow
- **AND** the skill MUST accept a `storage_ref` and an optional account identifier as inputs
- **AND** when the user offers only a local filesystem path, the skill MUST direct the runtime to ask for an upload instead of reading the path

#### Scenario: Import through the deterministic capability
- **WHEN** the skill guides an import of a CSV
- **THEN** the runtime MUST first call `import_transactions(storage_ref, dry_run=true)` and review `detected_format`, `error_details`, and `preview`
- **AND** it MUST then call `import_transactions(storage_ref)` without `dry_run`, passing the same `column_map` and `currency` when one was needed
- **AND** when the response has `status="unrecognized_format"`, it MUST build an explicit `column_map` from the returned `headers` and `missing_columns`, asking the owner only when the headers leave the mapping ambiguous or `headers_truncated` is true
- **AND** when the dry-run `preview` is empty, it MUST NOT run the import
- **AND** when no `column_map` can express the file's sign convention, it MUST report that to the owner instead of importing amounts with the wrong direction
- **AND** it MUST attempt at most 3 dry-run previews before reporting failure to the user

#### Scenario: Result reporting
- **WHEN** `import_transactions` completes without `dry_run`
- **THEN** the runtime MUST report to the user: total rows processed, rows imported, rows skipped (with explanation that these are duplicates), and any errors
- **AND** if `skipped` count exceeds 20% of `total`, the runtime MUST warn the user that some transactions may have been deduplicated and suggest verification

#### Scenario: Re-run after partial failure
- **WHEN** an import that failed part-way is run again
- **THEN** the runtime MUST explain to the user that previously imported rows will appear as "skipped (duplicate)" and this is expected, not data loss
- **AND** the runtime MUST report only the newly imported count as the meaningful result

### Requirement: Token budget protection
The skill MUST prevent the LLM from consuming excessive tokens during CSV import. This is a hard constraint, not a suggestion. The only CSV content that may enter the context MUST be what `import_transactions` returns: the dry-run `preview` and `error_details`.

ID: REQ-csv-extraction-skill-002
Source: finance-data-import "No caller-named local filesystem source"
Scope: v1-mandatory

#### Scenario: Prohibition on full-file context loading
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT place the CSV file into its context window
- **AND** the skill prompt MUST state the reason: "Reading a full CSV into context would consume hundreds of thousands of tokens and is not an acceptable approach"
- **AND** the LLM MUST see no more CSV rows than the `preview` of at most 10 transactions and the `error_details` that `import_transactions` returns

#### Scenario: Prohibition on per-row tool calls
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT call `record_transaction`, `record_transaction_fact`, or any single-record ingestion tool in a loop
- **AND** the skill prompt MUST state the reason: "Calling a tool per CSV row would generate thousands of MCP round-trips and consume an unacceptable number of tokens"
- **AND** the ONLY acceptable ingestion path MUST be `import_transactions(storage_ref)`

#### Scenario: Skill prompt emphasis
- **WHEN** the skill prompt is authored
- **THEN** the token budget constraints MUST appear in a prominently placed section (e.g., "CRITICAL CONSTRAINTS" or equivalent) near the top of the skill document, before the workflow steps
- **AND** the constraints MUST be stated in imperative form (MUST NOT, NEVER) not advisory form (should avoid, prefer not to)
