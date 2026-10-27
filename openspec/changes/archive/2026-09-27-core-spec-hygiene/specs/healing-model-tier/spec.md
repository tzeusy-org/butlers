## MODIFIED Requirements

### Requirement: Specialty Complexity Tier for Healing
Healing SHALL resolve models from the `specialty` tier of the canonical complexity enum (defined by complexity-classification "Complexity Enum", including its retired-value remapping).

#### Scenario: Enum exposes specialty
- **WHEN** the `Complexity` enum is used
- **THEN** `Complexity.SPECIALTY` has value `"specialty"` and healing resolves from it

#### Scenario: Catalog entry with specialty tier
- **WHEN** a model catalog entry is created with `complexity_tier = "specialty"`
- **THEN** the constraint passes and the entry is stored

#### Scenario: Deprecated self_healing alias remaps
- **WHEN** code or config emits the retired `self_healing` complexity value
- **THEN** it is remapped to `specialty` per complexity-classification "Legacy vocabulary remapping"

### Requirement: Dashboard Tier Visibility
The Model Settings UI at `/butlers/settings` SHALL display the canonical complexity tiers as selectable values in the tier dropdown when creating or editing catalog entries.

#### Scenario: Tiers appear in dropdown
- **WHEN** an operator opens the model settings page and clicks the tier dropdown
- **THEN** the dropdown lists exactly the canonical tiers defined by complexity-classification "Complexity Enum"

#### Scenario: Disabling all specialty models stops healing
- **WHEN** an operator disables all catalog entries with tier `specialty`
- **THEN** `resolve_model(any_butler, Complexity.SPECIALTY)` returns `None` for all butlers
- **AND** no new healing attempts can be spawned
- **NOTE** because `specialty` is shared with other specialty-class work, disabling it also affects that non-healing work, so it is not a healing-only kill switch

### Requirement: API Validation Update
The model settings API endpoints SHALL accept exactly the canonical tiers defined by complexity-classification "Complexity Enum" as valid `complexity_tier` values in request bodies. Healing entries use `specialty`.

#### Scenario: Create entry with specialty tier via API
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "specialty"`
- **THEN** validation passes and the entry is created

#### Scenario: Invalid tier still rejected
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "super_high"`
- **THEN** validation fails with a 422 error listing the valid canonical tiers
