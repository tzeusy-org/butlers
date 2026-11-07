## ADDED Requirements

### Requirement: Visit brief terminal Telegram outcomes

ID: REQ-butler-health-004
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

A visit brief with a known Telegram prefix or actual uncertain effect SHALL persist distinct failure metadata in its existing digest, leave delivered_at unset and exclude the same event from future sends. A failed digest write SHALL retain a process-local exact-event fence and reconcile before upcoming-window selection. Stored clinical/source digest fields SHALL remain unchanged. Ordinary failed delivery retains its existing retry behavior. This does not provide a durable chunk outbox or crash-atomic provider/database guarantee.

#### Scenario: Appointment window changes after failure

- **WHEN** a terminal brief has an incomplete digest write and then leaves or re-enters the upcoming window
- **THEN** the exact terminal digest is settled without recomposition, another send, or delivered credit
