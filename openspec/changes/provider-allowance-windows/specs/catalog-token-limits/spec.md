## ADDED Requirements

### Requirement: Provider Allowance Is Distinct From Owner Quota
Provider allowance (the provider's own cap on an account, recorded in
`public.provider_allowance_states`) SHALL be tracked separately from owner token quota
(`public.token_limits`). A provider usage-limit SHALL NOT write `token_limits` rows or
ledger quota resets, and an owner quota denial SHALL NOT write allowance state. The
allowance state table SHALL be keyed by account key, with `state` in
`available|exhausted|unknown` and `reset_source` in `parsed|default_window|unknown`.
After the switch allowance SHALL stay distinct from goal budgets as well: a usage-limit
SHALL NOT add to, refund or reset a goal's RFC 0038 D10 budget, and goal budget exhaustion
SHALL NOT write allowance state.

ID: REQ-catalog-token-limits-003
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D10; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

#### Scenario: Quota denial does not mark an account exhausted
- **WHEN** a catalog entry is denied by its owner-set token limit
- **THEN** no allowance state SHALL be written for its provider account

#### Scenario: Goal budget exhaustion does not mark an account exhausted
- **WHEN** after the switch a goal ends `budget_exhausted`
- **THEN** no allowance state SHALL be written for any provider account
