## ADDED Requirements

### Requirement: Telegram terminal effect outcomes

ID: REQ-butler-messenger-005
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

Messenger SHALL propagate a known Telegram prefix as delivery_error with partial_delivery true and retryable false. Actual selected Telegram transport uncertainty SHALL be distinct telegram_effect_uncertain with retryable false, without fabricated message IDs or complete-delivery credit. These outcome fields SHALL NOT provide approval, owner, actor or tool authority. Legacy candidate routing SHALL NOT try another provider after either terminal effect. Read-only and proven-not-attempted retry behavior SHALL remain unchanged. Authenticated recovery SHALL retain its existing provider-started ambiguous ledger fence.

#### Scenario: Terminal Telegram delivery

- **WHEN** the selected Telegram adapter confirms a prefix then fails or has an unknown effect receipt
- **THEN** Messenger and Switchboard retain the distinct terminal failure and no automatic next-candidate/reconnect repeats the effect
