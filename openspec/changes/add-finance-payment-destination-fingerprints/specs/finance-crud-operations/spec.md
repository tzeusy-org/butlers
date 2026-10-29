## ADDED Requirements

### Requirement: Bill tracking accepts a payment destination

`track_bill` SHALL accept an optional `payment_destination` (an account number, IBAN or payment handle exactly as the bill shows it) and SHALL stamp the bill with the destination verdict defined by the `finance-payment-destination-integrity` capability. Finance SHALL keep the raw value out of its own tables, the SPO mirror, its logs and the tool result (the surfaces listed by REQ-finance-payment-destination-integrity-001); session tool-call records currently keep raw tool arguments, a known exception tracked as bu-4mfhdq. Omitting the parameter SHALL leave existing callers and the return shape unchanged. The `tool-reference` skill and the MCP tool description SHALL document the parameter and tell the owner to confirm a `new_for_counterparty` bill by phone before paying.

ID: REQ-finance-crud-operations-001
Source: bu-s11n0s.21 payment-redirect guard S1
Scope: v1-mandatory

#### Scenario: Destination supplied

- **WHEN** `track_bill` is called with `payment_destination`
- **THEN** the returned bill carries `destination_verdict` and `destination_fingerprint`, and no field of the result contains the raw destination

#### Scenario: Destination omitted

- **WHEN** `track_bill` is called without `payment_destination`
- **THEN** `destination_fingerprint` and `destination_verdict` are NULL and every other field behaves as before
