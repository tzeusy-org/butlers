## ADDED Requirements

### Requirement: Terminal Telegram deferred tails

ID: REQ-time-aware-delivery-002
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

Both solo and coalesced deferred Telegram sends SHALL treat known-prefix and actual uncertain effects as terminal failed tails. They SHALL cancel the exact existing rows, retain delivered_at unset and record failed rather than delivered or coalesced credit. A failed terminal write SHALL be reconciled before fresh grouping, independently of due/expiry windows. Ordinary retryable failures SHALL preserve their existing pending-row retry behavior.

#### Scenario: Solo and digest failed tails

- **WHEN** a flushed solo row or same-recipient digest has a terminal Telegram effect
- **THEN** no later tick replays the prefix and the committed old rows have no delivered credit
