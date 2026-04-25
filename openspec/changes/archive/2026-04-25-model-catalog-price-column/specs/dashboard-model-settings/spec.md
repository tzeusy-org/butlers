## ADDED Requirements

### Requirement: Pricing Lookup API

The dashboard SHALL expose an endpoint that returns per-model token pricing from `pricing.toml` so the frontend can display costs inline.

#### Scenario: Fetch pricing map

- **WHEN** `GET /api/settings/pricing` is called
- **THEN** the response SHALL contain a JSON object mapping model IDs to their per-million-token prices
- **AND** each entry SHALL include `input_per_million` and `output_per_million` as numbers (USD)
- **AND** for tiered models, the lowest tier (context_threshold=0) prices SHALL be used

#### Scenario: Unknown model not in response

- **WHEN** a model exists in the catalog but has no entry in `pricing.toml`
- **THEN** that model_id SHALL NOT appear in the pricing map (absence signals unknown pricing)

#### Scenario: Zero-cost model

- **WHEN** a model has `input_price_per_token = 0.0` and `output_price_per_token = 0.0` in `pricing.toml`
- **THEN** the pricing map SHALL include that model with `input_per_million: 0` and `output_per_million: 0`

## MODIFIED Requirements

### Requirement: Model Catalog Settings UI

The dashboard settings page SHALL include a model catalog management section with full CRUD capabilities, an alias editor, and inline pricing display.

#### Scenario: Catalog table display

- **WHEN** the settings page loads the model catalog section
- **THEN** a table displays all catalog entries grouped by complexity tier with columns: Alias, Runtime, Model ID, Extra Args (formatted), Price (per 1M tokens), Priority, Enabled (toggle), 24h, 30d, and Actions (Edit, Delete)
- **AND** the `discretion` tier group is displayed under a "Discretion" heading, visually separated from session tiers, with a subtitle explaining these models are used for connector noise filtering

#### Scenario: Price column shows known pricing

- **WHEN** a catalog entry's `model_id` matches an entry in the pricing map (after stripping the `opencode-go/` prefix if present)
- **THEN** the Price column SHALL display the input and output prices formatted as `$X.XX / $Y.YY` per 1M tokens

#### Scenario: Price column shows free for zero-cost models

- **WHEN** a catalog entry's pricing has both input and output at zero
- **THEN** the Price column SHALL display `Free`

#### Scenario: Price column shows dash for unknown pricing

- **WHEN** a catalog entry's `model_id` has no match in the pricing map
- **THEN** the Price column SHALL display `–` (en-dash)
