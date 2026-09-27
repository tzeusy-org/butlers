## MODIFIED Requirements

### Requirement: General Butler Tool Surface
The general butler SHALL provide collection and item management tools for organizing freeform data.

ID: REQ-butler-general-001
Source: owner-adopted General composition; RFC 0037; existing butler-general tool surface
Scope: v1-mandatory

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the general butler
- **THEN** it has access to: `collection_create`, `collection_list`, `collection_delete`, `item_create`, `item_get`, `item_update`, `item_delete`, `item_search`, `collection_export`, and calendar tools

#### Scenario: Adopted ordinary capture availability remains configured and private-safe
- **WHEN** the General capture target is implemented and its existing configured group/module authority admits the capability
- **THEN** ordinary capture, explicit ordinary collection declaration and bounded capture search SHALL be available through their documented owner/source boundary
- **AND** existing ordinary collection/item creation semantics remain compatible, private-only names cannot resolve private identities, and no record-bearing custody tool is introduced
- **AND** disabled groups, missing source authority or unavailable privacy fencing SHALL NOT be bypassed to expose these tools or their data
