## MODIFIED Requirements

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

#### Scenario: Historical backfill of several statements
- **WHEN** the owner wants to import history spanning several statements or accounts
- **THEN** the runtime MUST obtain one `storage_ref` per statement and MUST NOT offer to discover statements in a local folder
- **AND** it MUST dry-run every statement before importing any, then import each statement whose preview was correct with its own `import_transactions(storage_ref)` call, holding back and reporting any statement whose preview failed
- **AND** it MUST report per-statement counts and the totals across statements, relying on `import_transactions` deduplication for rows shared by overlapping statements
