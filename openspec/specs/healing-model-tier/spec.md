# Healing Model Tier

## Purpose

A dedicated `self_healing` complexity tier in the Model Catalog, ensuring healing agents resolve models exclusively from this tier. Provides operators with independent cost and capability control over what powers self-healing, separate from normal butler work tiers.

## ADDED Requirements

### Requirement: Self-Healing Complexity Tier
The `Complexity` enum and the `complexity_tier` CHECK constraint on `shared.model_catalog` SHALL include `self_healing` as a valid tier value.

#### Scenario: Enum includes self_healing
- **WHEN** the `Complexity` enum is used
- **THEN** `Complexity.SELF_HEALING` has value `"self_healing"`

#### Scenario: Catalog entry with self_healing tier
- **WHEN** a model catalog entry is created with `complexity_tier = "self_healing"`
- **THEN** the CHECK constraint passes and the entry is stored

#### Scenario: Override to self_healing tier
- **WHEN** a butler model override sets `complexity_tier = "self_healing"` for a catalog entry
- **THEN** `resolve_model(butler_name, "self_healing")` includes that entry in candidates

### Requirement: Healing Agent Model Resolution
The healing dispatcher SHALL resolve models using `resolve_model(pool, butler_name, "self_healing")`. If no self-healing tier model is available, the healing attempt is NOT spawned.

#### Scenario: Self-healing model resolved
- **WHEN** `resolve_model("email", "self_healing")` returns `("claude", "claude-sonnet-4-6", [])`
- **THEN** the healing agent is spawned using the Claude Sonnet adapter

#### Scenario: No self-healing model available
- **WHEN** `resolve_model("email", "self_healing")` returns `None`
- **THEN** the healing attempt is skipped with a WARNING log
- **AND** no `healing_attempts` row is created

#### Scenario: Per-butler override for healing model
- **WHEN** butler `finance` has an override that remaps `claude-opus` to the `self_healing` tier
- **THEN** `resolve_model("finance", "self_healing")` returns the Opus model
- **AND** other butlers still use the global self-healing tier default

### Requirement: Seed Data for Self-Healing Tier
The `model_catalog_defaults.toml` SHALL include at least one default entry for the `self_healing` tier.

#### Scenario: Default self-healing model
- **WHEN** the catalog is seeded from defaults
- **THEN** an entry exists with tier `self_healing` (e.g. `claude-sonnet` at priority 10)
- **AND** the entry is enabled by default

### Requirement: Dashboard Tier Visibility
The Model Settings UI at `/butlers/settings` SHALL display `self_healing` as a selectable tier in the complexity tier dropdown when creating or editing catalog entries.

#### Scenario: Tier appears in dropdown
- **WHEN** an operator opens the model settings page and clicks the tier dropdown
- **THEN** `self_healing` appears alongside `trivial`, `medium`, `high`, `extra_high`, and `discretion`

#### Scenario: Disabling all self-healing models stops healing
- **WHEN** an operator disables all catalog entries with tier `self_healing`
- **THEN** `resolve_model(any_butler, "self_healing")` returns `None` for all butlers
- **AND** no new healing attempts can be spawned (acts as a global kill switch)

### Requirement: API Validation Update
The model settings API endpoints SHALL accept `self_healing` as a valid `complexity_tier` value in request bodies.

#### Scenario: Create entry with self_healing tier via API
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "self_healing"`
- **THEN** validation passes and the entry is created

#### Scenario: Invalid tier still rejected
- **WHEN** `POST /api/settings/models` is called with `complexity_tier: "super_high"`
- **THEN** validation fails with a 422 error listing valid tiers including `self_healing`
