## MODIFIED Requirements

### Requirement: Model Resolution
The system SHALL provide a `resolve_model(butler_name, complexity_tier)` function that selects the appropriate model configuration at spawn time by querying the catalog with butler-specific overrides applied.

#### Scenario: Resolution with global defaults only
- **WHEN** `resolve_model("finance", "medium")` is called and no overrides exist for `finance`
- **THEN** the function returns the enabled global catalog entry for tier `medium` with the lowest `priority` value
- **AND** the return value is a tuple of `(runtime_type, model_id, extra_args, catalog_entry_id)`

#### Scenario: Resolution with butler overrides
- **WHEN** `resolve_model("switchboard", "trivial")` is called and an override remaps a `medium` entry to `trivial` for `switchboard`
- **THEN** the remapped entry is included in the candidate set for `trivial`

#### Scenario: Resolution with disabled override
- **WHEN** `resolve_model("health", "high")` is called and an override disables the preferred `high` entry for `health`
- **THEN** the disabled entry is excluded and the next-priority `high` entry is selected

#### Scenario: No candidates fallback
- **WHEN** `resolve_model(butler_name, complexity_tier)` finds no enabled entries for the requested tier
- **THEN** the function returns `None`
- **AND** the caller (spawner) falls back to `butler.toml` `[butler.runtime].model`

#### Scenario: Priority tie-breaking
- **WHEN** multiple enabled entries exist for the same butler+tier with the same effective priority
- **THEN** the entry with the earliest `created_at` is selected (stable ordering)

#### Scenario: Return type includes catalog_entry_id
- **WHEN** `resolve_model()` returns a match
- **THEN** the return type is `tuple[str, str, list[str], UUID]` — `(runtime_type, model_id, extra_args, catalog_entry_id)`
- **AND** `catalog_entry_id` is the UUID primary key of the matched `shared.model_catalog` row

## ADDED Requirements

### Requirement: Adapter Token Reporting Contract
All runtime adapters SHALL return `input_tokens` and `output_tokens` in their usage dict from `invoke()`.

#### Scenario: Adapter reports token usage
- **WHEN** a runtime adapter completes an invocation
- **THEN** it returns a usage dict containing at minimum `{"input_tokens": int, "output_tokens": int}`

#### Scenario: Adapter cannot determine token counts
- **WHEN** a runtime adapter genuinely cannot determine token counts (e.g., CLI process does not expose them)
- **THEN** it returns `{}` or `None` for usage
- **AND** the ledger does not record a row for that invocation

#### Scenario: Known adapters to audit
- **WHEN** the adapter token reporting contract is enforced
- **THEN** the following adapters are verified: `claude`, `codex`, `gemini`, `opencode` (including ollama via opencode)
