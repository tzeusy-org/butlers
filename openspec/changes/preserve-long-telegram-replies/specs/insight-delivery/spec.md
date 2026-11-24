## ADDED Requirements

### Requirement: Telegram Insight failed-tail admission

ID: REQ-insight-delivery-002
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

Insight delivery SHALL retain known-prefix or actual uncertain Telegram failure metadata on the existing candidate and filter it from subsequent delivery. Such a tail SHALL NOT create delivery, engagement, cooldown or complete-message credit. Failed terminal bookkeeping SHALL be reconciled for the exact old candidates before fresh selection; ordinary failures retain their existing attempt/retry behavior. Distinct new candidates SHALL NOT inherit an old candidate failure.

#### Scenario: Terminal candidate readback

- **WHEN** an Insight send reports a terminal Telegram effect
- **THEN** the candidate is filtered with failure metadata and independent readback shows no delivery or engagement, with no second send
