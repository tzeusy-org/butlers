## ADDED Requirements

### Requirement: Terminal Telegram notifier propagation

ID: REQ-proactive-insight-engine-036
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

The actual registered Insight notifier SHALL preserve fixed server known-prefix and uncertain-effect fields through its error result. Scheduled delivery SHALL NOT infer success from the presence of a transport reply or lose the terminal outcome while adapting notify and delivery-cycle result shapes. The existing owner attention, premise, currentness and OFF gates SHALL remain authoritative.

#### Scenario: Notifier to scheduled admission

- **WHEN** the actual delivery consumer returns a terminal Telegram result
- **THEN** the scheduled notifier retains its distinct no-replay metadata and the cycle records a failed tail rather than delivered success
