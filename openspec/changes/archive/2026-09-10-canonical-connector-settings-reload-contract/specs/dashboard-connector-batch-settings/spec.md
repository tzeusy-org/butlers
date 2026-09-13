## MODIFIED Requirements

### Requirement: Batch Settings Card on Connector Detail Page

The connector detail page SHALL display an editable card for batch-related
settings when the connector type is `telegram_user_client` or
`whatsapp_user_client`.

#### Scenario: Batch settings card displayed
- **WHEN** the user navigates to a connector detail page for
  `telegram_user_client` or `whatsapp_user_client`
- **THEN** a "Batch Settings" card is displayed alongside existing cards
  (Discretion Settings, Ingestion Rules)
- **AND** the card shows the current `flush_interval_s` value with an editable
  input

#### Scenario: Batch settings card hidden for non-batch connectors
- **WHEN** the user navigates to a connector detail page for `gmail` or
  `telegram-bot`
- **THEN** the "Batch Settings" card is NOT displayed

#### Scenario: Flush interval editable
- **WHEN** the user edits the `flush_interval_s` value on the batch settings card
- **THEN** the new value is submitted via
  `PATCH /api/ingestion/connectors/{type}/{identity}/settings` with
  `{"settings": {"flush_interval_s": <value>}}`
- **AND** the card shows the updated value after successful save

#### Scenario: Flush interval validation
- **WHEN** the user enters a `flush_interval_s` value less than 60 or greater
  than 7200
- **THEN** the card displays a validation error
- **AND** the value is not submitted

#### Scenario: Effective value display
- **WHEN** the batch settings card is displayed
- **THEN** it shows the effective value (dashboard setting if set, otherwise env
  var default)
- **AND** a label indicates whether the value is "custom" (dashboard-set) or
  "default"
