## ADDED Requirements

### Requirement: Terminal Telegram notification retry fence

ID: REQ-core-notify-039
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

The actual deferred notifier and credential-transition caller SHALL NOT turn a known-prefix or server-derived uncertain Telegram effect into a retryable ordinary failure. Deferred rows SHALL settle cancelled with failed attention outcome and no delivered timestamp. Credential transitions SHALL supersede pending retry envelopes and write their existing debounce record without claiming complete delivery. An incomplete bookkeeping write SHALL fence subsequent automatic admission in the same process until the exact old work settles. A fresh unrelated notification SHALL remain eligible. No new durable chunk outbox or crash-atomic provider/database claim is introduced.

#### Scenario: Bookkeeping failure

- **WHEN** a terminal Telegram effect occurs and its first terminal-store write fails
- **THEN** later admissions settle the same old work without sending it again and do not cancel a new same-recipient row without an attempt
