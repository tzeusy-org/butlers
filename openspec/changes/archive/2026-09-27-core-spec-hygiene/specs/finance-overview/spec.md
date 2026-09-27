## MODIFIED Requirements

### Requirement: Currency-honest aggregate projection

Finance aggregate readers SHALL never imply that amounts in different currencies share one
denomination. Spending summaries, net-worth history, cash flow, subscription annual cost,
upcoming-bill totals, and tax summaries SHALL retain additive legacy totals for compatibility but
also expose canonical per-currency buckets. A legacy total that numerically adds more than one
currency SHALL be explicitly marked degraded and SHALL NOT carry a fabricated currency label.

ID: REQ-finance-overview-currency-honesty-001
Source: RFC 0012; Jarvis pursuit run 11 move 5

#### Scenario: Single-currency aggregate carries its real currency

- **WHEN** every monetary row contributing to an aggregate has the same ISO currency
- **THEN** the response `currency` is that real currency
- **AND** `by_currency` contains exactly one bucket with the same totals
- **AND** `legacy_aggregate_degraded` is false

#### Scenario: Mixed currencies are separated without conversion

- **WHEN** two or more currencies contribute to an aggregate and no owner-sourced FX conversion
  is configured
- **THEN** `by_currency` contains one independently computed bucket per currency
- **AND** the legacy numeric total carries `legacy_aggregate_degraded=true` and
  `degraded_reason="multiple_currencies_unconverted"`
- **AND** its `currency` is null, never `USD`, the majority currency, or `MAX(currency)`
- **AND** a dashboard consumer renders the per-currency values instead of formatting the degraded
  legacy number as money

#### Scenario: Own-account transfers do not affect cash flow

- **WHEN** cash flow contains debit and credit rows classified with the canonical `transfer`
  category
- **THEN** those rows contribute to neither income nor expense, net, nor savings rate
- **AND** each remaining currency computes its own savings rate from its own income and expense
