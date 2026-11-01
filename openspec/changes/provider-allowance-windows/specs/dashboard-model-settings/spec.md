## ADDED Requirements

### Requirement: Provider Allowance Observation
The dashboard SHALL expose an owner-only, read-only `GET /api/settings/models/allowance` that
projects `public.provider_allowance_states` per account, guarded by the same owner dependency as
`GET /api/settings/models/attention`. Each account SHALL carry `account_key`, `state`
(`available`, `exhausted` or `unknown`), `reset_at`, `reset_source` (`parsed`, `default_window` or
`unknown`), `first_seen_at`, `updated_at` and the catalog `entries` (`id`, `alias`) whose
`COALESCE(allowance_account, runtime_type)` is that account. A read failure SHALL return the
degraded envelope `available: false` and SHALL log without row content. The endpoint SHALL NOT
write, reset or probe anything.

The Models tab SHALL render an Allowance strip: an exhausted account shows a live countdown to
`reset_at` labelled "provider said" (`parsed`) or "estimated" (`default_window`); an account whose
`reset_at` has passed but is still exhausted reads "should be back", never a negative countdown;
an `unknown` account reads "unknown"; available accounts collapse to a count. The `/system`
verdict SHALL add an allowance source with the clause "{account} out of allowance until {time}"
linking to `/settings/models`; an unreadable table SHALL read "allowance unknown" and SHALL NOT
render all-clear, and a non-owner (403) SHALL be silent like the egress source.

ID: REQ-dashboard-model-settings-005
Source: bu-q7vx1q.50; openspec change provider-allowance-windows task 3.1
Scope: v1-mandatory

#### Scenario: Exhausted account shows a labelled countdown
- **WHEN** an account is `exhausted` with `reset_source = parsed` and a future `reset_at`
- **THEN** the Models tab shows a countdown to `reset_at` labelled "provider said"
- **AND** the `/system` verdict names the account with a door to `/settings/models`

#### Scenario: Passed reset reads should be back
- **WHEN** an account is still `exhausted` but `reset_at` is in the past
- **THEN** the strip reads "should be back" and shows no negative countdown

#### Scenario: Unreadable table is not all-clear
- **WHEN** the endpoint returns `available: false` or the request fails for a non-403 reason
- **THEN** the strip and the `/system` verdict read "allowance unknown"
- **AND** the verdict does not render its all-clear line
