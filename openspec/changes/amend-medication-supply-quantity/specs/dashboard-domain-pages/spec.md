## ADDED Requirements

### Requirement: Medications page exposes honest supply quantity

The dashboard Medications page SHALL provide one optional supply-quantity field
in the existing create/edit form. The field SHALL accept only a positive whole
number when present, SHALL send the value through the typed Health medication
API on create or edit, and SHALL use the existing edit quantity update as the
refill/current-supply path. A missing server quantity SHALL render explicitly
as `Supply: unknown`; the page SHALL never render zero or fabricate a supply
from dosage, frequency, or adherence data.

#### Scenario: The owner records an initial supply while creating a medication

- **WHEN** the owner enters a positive whole-number supply quantity and submits
  the create form
- **THEN** the dashboard SHALL send that exact `quantity` in
  `POST /api/health/medications` and render the returned medication's count

#### Scenario: The owner records a refill from the edit form

- **WHEN** the owner enters a new positive whole-number supply quantity for an
  existing medication and saves the edit form
- **THEN** the dashboard SHALL send that exact `quantity` in
  `PUT /api/health/medications/{id}`
- **AND** it SHALL continue to rely on the server-returned quantity and
  `quantity_updated_at`, not client-side depletion math

#### Scenario: An omitted supply is visibly unknown

- **WHEN** the medication response has `quantity: null` or omits a legacy
  quantity value
- **THEN** the medication row SHALL render `Supply: unknown`
- **AND** it SHALL NOT render `Supply: 0`, a standard pack size, or an
  adherence-derived estimate

#### Scenario: A blank edit does not erase known supply

- **WHEN** an existing medication has a recorded quantity and the owner saves
  an edit without entering a replacement quantity
- **THEN** the dashboard SHALL preserve the existing quantity by omitting the
  quantity field from the update payload

#### Scenario: Invalid form values are refused before submission

- **WHEN** the owner enters zero, a negative number, a decimal, or malformed
  text in the supply field
- **THEN** the form SHALL show a typed positive-whole-number validation message
- **AND** it SHALL NOT call the create or update mutation

## Source References

- Non-Negotiable Rule 1 (owner sovereignty over personal health data)
- Non-Negotiable Rule 6 (the Health manifesto governs medication scope)
- RFC 0007 (Dashboard and API Surface)
