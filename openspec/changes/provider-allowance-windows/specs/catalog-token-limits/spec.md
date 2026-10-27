## ADDED Requirements

### Requirement: Provider Allowance Is Distinct From Owner Quota
Provider allowance (the provider's own cap on an account, recorded in
`public.provider_allowance_states`) SHALL be tracked separately from owner token quota
(`public.token_limits`). A provider usage-limit SHALL NOT write `token_limits` rows or
ledger quota resets, and an owner quota denial SHALL NOT write allowance state. The
allowance state table SHALL be keyed by account key, with `state` in
`available|exhausted|unknown` and `reset_source` in `parsed|default_window|unknown`.

#### Scenario: Quota denial does not mark an account exhausted
- **WHEN** a catalog entry is denied by its owner-set token limit
- **THEN** no allowance state SHALL be written for its provider account
