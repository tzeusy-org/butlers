## ADDED Requirements

### Requirement: Purpose lane preserves canonical model resolution

A dispatch purpose lane SHALL be observational evidence and SHALL NOT by itself alter catalog
eligibility, priority, effective tier, fit, verification, quota, breaker, provider/runtime
selection, or same-tier failover. `private_content` SHALL NOT require a local runtime, an `ollama/`
model, locality proof, or a special audited remote-model exception. Separately adopted operator
routing rules remain subject to their ordinary authority and evaluation contracts.

#### Scenario: Private-content source uses ordinary catalog selection
- **WHEN** trusted WhatsApp or Telegram context labels a dispatch `private_content`
- **THEN** candidate selection and failover apply the same canonical catalog contracts used for `standard`
- **AND** no candidate is preferred or excluded solely because it is local or remote

#### Scenario: Eligible remote candidate is not refused
- **WHEN** a `private_content` dispatch has an ordinarily eligible remote candidate and no eligible local candidate
- **THEN** routing may invoke that remote candidate under the normal catalog and operator-routing gates
- **AND** it does not require a private-purpose audit exception or emit `private_content_remote_refused`

#### Scenario: Purpose lane does not widen authority
- **WHEN** a dispatch carries either purpose lane
- **THEN** the lane neither bypasses nor replaces fit, verification, quota, breaker, permission, budget, or separately adopted operator-rule checks

### Requirement: Content-blind purpose-lane evidence

Dispatch attempts and token-usage evidence SHALL carry a separate closed `purpose_lane` without
using a raw connector identity as a butler or purpose-lane label. Existing open-ended spend-purpose
fields retain their established meaning.

#### Scenario: Private discretion spend is attributed safely
- **WHEN** a WhatsApp or Telegram discretion adapter reports usage
- **THEN** the usage and dispatch evidence records `private_content`
- **AND** its grouping identity contains no phone, chat, sender, recipient, or thread identifier
