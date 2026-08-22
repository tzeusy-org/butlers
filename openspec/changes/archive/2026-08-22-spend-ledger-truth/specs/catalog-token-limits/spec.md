## ADDED Requirements

### Requirement: Truthful ledger pricing for the monthly ceiling
The monthly spend ceiling SHALL price the current month from the append-only
token usage ledger and SHALL preserve the distinction between a known
zero-marginal model and a model with no configured price.

#### Scenario: Known zero marginal cost remains priced
- **WHEN** a ledger row resolves to a pricing entry classified as `subscription` or `local` with zero rates
- **THEN** its cost contribution is numeric `0.0` and it is not reported as unpriced.

#### Scenario: Missing pricing remains an omission
- **WHEN** a current-month ledger row resolves to a model ID with no pricing entry
- **THEN** `price_mtd_from_ledger` returns the known priced subtotal together with that model's observed usage in an unpriced-model envelope.
- **AND** `check_monthly_ceiling` evaluates the existing policy against only the known priced subtotal and preserves the unpriced envelope in its status instead of converting the omission to zero.

#### Scenario: Ceiling blindness is visible
- **WHEN** the current month contains usage for one or more unpriced models
- **THEN** the Spend forecast API exposes the distinct unpriced-model count alongside the same ceiling calculation used by the spawn gate.
- **AND** the dashboard states that the ceiling is blind to that count, rather than presenting the priced subtotal as complete spend.
