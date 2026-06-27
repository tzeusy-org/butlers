# Healing Model Tier

## Purpose

Healing agents resolve models through the shared `specialty` complexity tier of the Model Catalog. The previously dedicated `self_healing` tier was retired in migration core_093 and folded into `specialty`; operators control what powers healing by managing the `specialty` tier entries. Note that `specialty` is a shared tier, so changes to it also affect other specialty-class work, not healing alone.

## Requirements

### Requirement: Specialty Complexity Tier for Healing
The `Complexity` enum and the `complexity_tier` constraint on `public.model_catalog` SHALL use the canonical tiers (`reasoning`, `workhorse`, `cheap`, `specialty`, `local`, `legacy`). Healing resolves models from the `specialty` tier. The legacy value `self_healing` is accepted only as a deprecated alias that remaps to `specialty` with a logged warning (retired in migration core_093).

#### Scenario: Enum exposes specialty
- **WHEN** the `Complexity` enum is used
- **THEN** `Complexity.SPECIALTY` has value `"specialty"` and healing resolves from it

#### Scenario: Catalog entry with specialty tier
- **WHEN** a model catalog entry is created with `complexity_tier = "specialty"`
- **THEN** the constraint passes and the entry is stored

#### Scenario: Deprecated self_healing alias remaps
- **WHEN** code or config emits the legacy `self_healing` complexity value
- **THEN** it is remapped to `specialty` and a warning is logged noting the old vocabulary was retired in migration core_093

### Requirement: Healing Agent Model Resolution
The healing dispatcher SHALL resolve models using `resolve_model(pool, butler_name, Complexity.SPECIALTY)`. If no specialty tier model is available, the healing attempt is NOT spawned.

#### Scenario: Specialty model resolved
- **WHEN** `resolve_model("email", Complexity.SPECIALTY)` returns `("claude", "claude-sonnet-4-6", [])`
- **THEN** the healing agent is spawned using the Claude Sonnet adapter

#### Scenario: No specialty model available
- **WHEN** `resolve_model("email", Complexity.SPECIALTY)` returns `None`
- **THEN** the healing attempt is skipped with a WARNING log
- **AND** no `healing_attempts` row is created

#### Scenario: Per-butler override for healing model
- **WHEN** butler `finance` has an override that remaps `claude-opus` to the `specialty` tier
- **THEN** `resolve_model("finance", Complexity.SPECIALTY)` returns the Opus model
- **AND** other butlers still use the global specialty tier default

### Requirement: Seed Data for Specialty Tier
The `model_catalog_defaults.toml` SHALL include at least one default entry for the `specialty` tier.

#### Scenario: Default specialty model
- **WHEN** the catalog is seeded from defaults
- **THEN** at least one entry exists with `complexity_tier = "specialty"` (see the `specialty`-tier `[[models]]` entries in `model_catalog_defaults.toml`)
- **AND** the entry is enabled by default

### Requirement: Dashboard Tier Visibility
The Model Settings UI at `/butlers/settings` SHALL display the canonical complexity tiers as selectable values in the tier dropdown when creating or editing catalog entries.

#### Scenario: Tiers appear in dropdown
- **WHEN** an operator opens the model settings page and clicks the tier dropdown
- **THEN** the dropdown lists the canonical tiers `reasoning`, `workhorse`, `cheap`, `specialty`, `local`, and `legacy` (the old vocabulary trivial/medium/high/extra_high/discretion/self_healing was retired in migration core_093)

#### Scenario: Disabling all specialty models stops healing
- **WHEN** an operator disables all catalog entries with tier `specialty`
- **THEN** `resolve_model(any_butler, Complexity.SPECIALTY)` returns `None` for all butlers
- **AND** no new healing attempts can be spawned
- **NOTE** because `specialty` is shared with other specialty-class work, disabling it also affects that non-healing work, so it is not a healing-only kill switch

### Requirement: API Validation Update
The model settings API endpoints SHALL accept the canonical tiers (`reasoning`, `workhorse`, `cheap`, `specialty`, `local`, `legacy`) as valid `complexity_tier` values in request bodies. Healing entries use `specialty`.

#### Scenario: Create entry with specialty tier via API
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "specialty"`
- **THEN** validation passes and the entry is created

#### Scenario: Invalid tier still rejected
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "super_high"`
- **THEN** validation fails with a 422 error listing the valid canonical tiers
