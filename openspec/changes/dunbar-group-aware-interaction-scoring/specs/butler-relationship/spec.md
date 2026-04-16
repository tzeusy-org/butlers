## MODIFIED Requirements

### Requirement: Relationship Butler Tool Surface — Dunbar Tier
The relationship butler SHALL expose Dunbar tier management and group interaction tools.

#### Scenario: Dunbar tier tool in tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it MUST have access to `dunbar_tier_set(contact_id, tier)` for setting or clearing manual Dunbar tier overrides
- **AND** `contact_get` and `contact_search` responses MUST include `dunbar_tier` and `dunbar_score` fields
- **AND** it MUST have access to `interaction_log_group(group_id, direction, occurred_at, summary)` for logging interactions with all members of a contact group in a single call
