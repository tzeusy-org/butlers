## MODIFIED Requirements

### Requirement: CSV extraction skill definition
The finance butler MUST have a `transaction-csv-extraction` skill in `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md` that guides a goal invocation through adaptive CSV import using only the finance `import_transactions` capability. The skill MUST NOT prescribe reading files, writing or executing scripts, or calling HTTP endpoints, because goal invocations have no shell, filesystem, or web fetch.

Source: RFC 0038 D2 and D3
Scope: v1-mandatory

#### Scenario: Skill discovery
- **WHEN** a goal invocation searches finance guidance for the `transaction-csv-extraction` skill
- **THEN** the skill MUST be loadable through `guidance.load` from `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md`
- **AND** the skill MUST have valid frontmatter with `name`, `description`, and `version` fields

#### Scenario: Skill activation trigger
- **WHEN** a user provides a CSV file path and requests transaction import
- **THEN** the invocation handling the goal MUST load the `transaction-csv-extraction` skill to guide the workflow
- **AND** the skill MUST accept a file path and an optional account identifier as inputs

#### Scenario: Import through the deterministic capability
- **WHEN** the skill guides an import of a CSV file
- **THEN** the invocation MUST first call `import_transactions` with `dry_run=true` and review `detected_format`, `error_details`, and `preview`
- **AND** it MUST then call `import_transactions` without `dry_run`, passing the same `column_map` when one was needed
- **AND** when the response has `status="unrecognized_format"`, it MUST build an explicit `column_map` from the returned headers or ask the owner for one
- **AND** when no `column_map` can express the file's sign convention, it MUST report that to the owner instead of importing amounts with the wrong direction

#### Scenario: Result reporting
- **WHEN** `import_transactions` completes without `dry_run`
- **THEN** the invocation MUST report to the user: total rows processed, rows imported, rows skipped (with explanation that these are duplicates), and any errors
- **AND** if `skipped` count exceeds 20% of `total`, the invocation MUST warn the user that some transactions may have been deduplicated and suggest verification

#### Scenario: Re-run after partial failure
- **WHEN** an import that failed part-way is run again
- **THEN** the invocation MUST explain to the user that previously imported rows will appear as "skipped (duplicate)" and this is expected, not data loss
- **AND** the invocation MUST report only the newly imported count as the meaningful result

### Requirement: Token budget protection
The skill MUST prevent the LLM from consuming excessive tokens during CSV import. This is a hard constraint, not a suggestion. The only CSV content that may enter an invocation's context MUST be what `import_transactions` returns: detected headers, the dry-run preview, and per-row error details.

Source: RFC 0038 D3
Scope: v1-mandatory

#### Scenario: Prohibition on full-file context loading
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT place the entire CSV file into its context window
- **AND** the skill prompt MUST state the reason: "Reading a full CSV into context would consume hundreds of thousands of tokens and is not an acceptable approach"
- **AND** the invocation MUST see no more CSV rows than the `preview` of at most 10 transactions and the `error_details` that `import_transactions` returns

#### Scenario: Prohibition on per-row tool calls
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT call `record_transaction`, `record_transaction_fact`, or any single-record ingestion tool in a loop
- **AND** the skill prompt MUST state the reason: "Calling a tool per CSV row would generate thousands of MCP round-trips and consume an unacceptable number of tokens"
- **AND** the ONLY acceptable ingestion path MUST be the `import_transactions` capability

#### Scenario: Skill prompt emphasis
- **WHEN** the skill prompt is authored
- **THEN** the token budget constraints MUST appear in a prominently placed section (e.g., "CRITICAL CONSTRAINTS" or equivalent) near the top of the skill document, before the workflow steps
- **AND** the constraints MUST be stated in imperative form (MUST NOT, NEVER) not advisory form (should avoid, prefer not to)

## REMOVED Requirements

### Requirement: Script-generation workflow
**Reason**: RFC 0038 D3 disables runtime built-in tools ("no shell, filesystem, web fetch or local MCP servers"), so no goal invocation can sample a file from disk, write a Python script, execute it, or read its stdout.
**Migration**: Parsing, format detection, sign and date normalization, deduplication, dry-run preview, and the import summary are performed deterministically by `import_transactions` (`finance-data-import`); the skill now guides calls to that capability, and its result-reporting and re-run guidance moves to `CSV extraction skill definition`.

### Requirement: API connectivity verification
**Reason**: There is no generated script to run a pre-flight check, and RFC 0038 D3 gives sessions no network path except the capability broker.
**Migration**: Reachability of the finance butler is the control plane's concern; an unreachable `import_transactions` capability returns a broker error that the invocation reports.

### Requirement: Dashboard API URL discovery
**Reason**: Sessions cannot reach the dashboard API or read environment variables under RFC 0038 D3, so a base URL is meaningless to them.
**Migration**: None needed; imports go through the `import_transactions` capability, which the broker lists under its namespaced name.
