# Dunbar Tier Scoring

## Purpose

Dunbar tier scoring assigns every person-entity known to the relationship butler to a concentric social layer (5/15/50/150/500/1500) based on interaction patterns, and computes a decay-weighted health score that drives reach-out prioritization.

## ADDED Requirements

### Requirement: Decay score computation from interaction history
The system SHALL compute a decay score for each contact by summing exponentially decayed contributions from all recorded interactions.

#### Scenario: Score for a contact with recent interactions
- **WHEN** a contact has interactions recorded as temporal facts (`predicate='interaction'`, `scope='relationship'`)
- **THEN** the decay score MUST be computed as `sum(exp(-lambda * days_since_interaction_i))` for each interaction fact
- **AND** `lambda` MUST equal `ln(2) / 30` (30-day half-life)
- **AND** `days_since_interaction_i` MUST be computed from each interaction's `valid_at` relative to the current timestamp

#### Scenario: Score for a contact with no interactions
- **WHEN** a contact has zero interaction facts
- **THEN** the decay score MUST be `0.0`

#### Scenario: Score rewards frequency and recency
- **WHEN** contact A has 10 interactions in the past 30 days and contact B has 1 interaction 5 days ago
- **THEN** contact A's decay score MUST be greater than contact B's decay score

#### Scenario: Score computation excludes inactive facts
- **WHEN** computing the decay score for a contact
- **THEN** the computation MUST only include interaction facts with `validity='active'`
- **AND** superseded, expired, and retracted facts MUST be excluded

---

### Requirement: Scoring eligibility — entity-contact linkage
Dunbar scoring SHALL only apply to person-entities that have a linked contact record in `public.contacts`.

#### Scenario: Person-entity with linked contact is scored
- **WHEN** a person-entity has a non-NULL `public.contacts.entity_id` linking it to a contact record
- **THEN** the entity MUST be included in Dunbar tier computation
- **AND** interactions MUST be resolved via `subject='contact:{contact_id}'` where `contact_id` is the linked contact's ID

#### Scenario: Person-entity without linked contact is unscored
- **WHEN** a person-entity has no linked contact record in `public.contacts`
- **THEN** the entity MUST have `dunbar_tier: null` and `dunbar_score: null`
- **AND** the entity MUST be excluded from rank-based tier assignment

#### Scenario: Archived contacts excluded from ranking
- **WHEN** a contact has `listed = false` (archived)
- **THEN** the contact MUST be excluded from Dunbar rank computation
- **AND** the contact MUST NOT occupy a tier slot
- **AND** the contact MUST still report its last-computed `dunbar_tier` and `dunbar_score` if queried directly, but marked as stale

---

### Requirement: Rank-based Dunbar tier assignment
The system SHALL assign each contact to a Dunbar tier by ranking all listed contacts with linked entities by decay score and mapping rank positions to fixed layer boundaries.

#### Scenario: Tier assignment from rank position
- **WHEN** all listed contacts with linked entities are sorted by decay score in descending order
- **THEN** ranks 1–5 MUST be assigned tier 5 (support clique)
- **AND** ranks 6–15 MUST be assigned tier 15 (sympathy group)
- **AND** ranks 16–50 MUST be assigned tier 50 (good friends)
- **AND** ranks 51–150 MUST be assigned tier 150 (meaningful contacts)
- **AND** ranks 151–500 MUST be assigned tier 500 (acquaintances)
- **AND** ranks 501+ MUST be assigned tier 1500 (recognizable)

#### Scenario: Zero-score contacts default to tier 1500
- **WHEN** a contact has a decay score of `0.0` and no manual tier override
- **THEN** the contact MUST be assigned tier 1500

#### Scenario: Tier boundary hysteresis
- **WHEN** a contact's rank crosses a tier boundary downward (e.g., from rank 5 to rank 6)
- **THEN** the contact MUST NOT drop to the lower tier until their rank exceeds the boundary by 2 positions (e.g., must reach rank 8+ to drop from tier 5 to tier 15)
- **AND** upward tier transitions MUST apply immediately with no hysteresis

#### Scenario: Layer sizes are fixed
- **WHEN** the system computes tier assignments
- **THEN** the layer sizes (5, 15, 50, 150, 500, 1500) MUST NOT be configurable by the user
- **AND** the layer sizes MUST remain constant across all installations

---

### Requirement: Manual tier override via SPO fact
The system SHALL allow manual tier overrides stored as property facts that pin a contact to a specific Dunbar tier regardless of computed score.

#### Scenario: Setting a manual tier override
- **WHEN** `dunbar_tier_set(contact_id, tier)` is called with a valid tier value (5, 15, 50, 150, 500, or 1500)
- **THEN** a property fact MUST be stored with `predicate='dunbar_tier_override'`, `content='{tier}'`, `entity_id=contact_entity_id`, `scope='relationship'`
- **AND** the fact MUST supersede any existing active `dunbar_tier_override` fact for that entity

#### Scenario: Override takes precedence over computed tier
- **WHEN** a contact has an active `dunbar_tier_override` fact with `content='5'`
- **THEN** the contact MUST be assigned tier 5 regardless of their computed rank position
- **AND** the contact MUST still be sorted by decay score within that tier

#### Scenario: Clearing a manual override
- **WHEN** `dunbar_tier_set(contact_id, null)` is called
- **THEN** the active `dunbar_tier_override` fact MUST be retracted
- **AND** the contact MUST revert to rank-based tier assignment

#### Scenario: Invalid tier value rejected
- **WHEN** `dunbar_tier_set` is called with a tier value not in (5, 15, 50, 150, 500, 1500)
- **THEN** the tool MUST raise a validation error with an actionable message listing valid values

---

### Requirement: Tier-aware cadence thresholds
Each Dunbar tier SHALL have a default expected contact cadence that determines when a contact is considered overdue.

#### Scenario: Default cadence per tier
- **WHEN** the system evaluates whether a contact is overdue
- **THEN** the default cadence thresholds MUST be: tier 5 = 14 days, tier 15 = 21 days, tier 50 = 45 days, tier 150 = 120 days, tier 500 = 270 days
- **AND** tier 1500 contacts MUST NOT be considered overdue under default cadence (no proactive suggestions)

#### Scenario: stay_in_touch_days overrides tier cadence
- **WHEN** a contact has a non-NULL `stay_in_touch_days` value
- **THEN** that value MUST be used as the overdue threshold instead of the tier's default cadence
- **AND** this applies regardless of the contact's Dunbar tier, including tier 1500

#### Scenario: Overdue determination
- **WHEN** a contact's days since last interaction exceeds their effective cadence (tier default or `stay_in_touch_days`)
- **THEN** the contact MUST be considered overdue
- **AND** a contact with no interactions and an effective cadence MUST be considered overdue

---

### Requirement: Unified urgency ranking for reach-out suggestions
The system SHALL rank overdue contacts by a tier-weighted urgency formula that combines overdue severity, tier importance, and contextual signals.

#### Scenario: Urgency score computation
- **WHEN** the system generates reach-out suggestions
- **THEN** each overdue contact's urgency MUST be computed as: `(days_overdue / tier_cadence) * tier_weight + context_bonus`
- **AND** `tier_weight` MUST be: tier 5 = 5.0, tier 15 = 3.0, tier 50 = 2.0, tier 150 = 1.0, tier 500 = 0.5

#### Scenario: Context bonuses
- **WHEN** computing urgency for an overdue contact
- **THEN** the context bonus MUST include: +2.0 if the contact has an important date within 14 days, +1.0 if the contact has a pending gift (active gift fact with status not 'given'), +0.5 if the contact's most recent note fact contains positive emotional context

#### Scenario: Non-overdue contacts excluded from urgency ranking
- **WHEN** a contact's days since last interaction is less than their effective cadence
- **THEN** the contact MUST have an urgency score of 0.0
- **AND** the contact MUST only appear in suggestions if they have a non-zero context bonus

#### Scenario: Tier 1500 exclusion
- **WHEN** the system generates reach-out suggestions
- **THEN** contacts assigned to tier 1500 MUST be excluded unless they have a non-NULL `stay_in_touch_days` value

#### Scenario: Suggestions are ordered by urgency descending
- **WHEN** the weekly `relationship-maintenance` task generates suggestions
- **THEN** the suggestions MUST be ordered by urgency score descending
- **AND** the default number of suggestions MUST be 3

---

### Requirement: Enriched contact response with Dunbar data
The system SHALL include computed Dunbar tier and decay score in contact retrieval responses.

#### Scenario: contact_get includes Dunbar fields
- **WHEN** `contact_get` is called for a contact
- **THEN** the response MUST include `dunbar_tier` (integer: 5, 15, 50, 150, 500, or 1500) and `dunbar_score` (float, rounded to 2 decimal places)
- **AND** if the contact has a manual override, `dunbar_tier_override` MUST be included as a boolean `true`

#### Scenario: contact_search includes Dunbar fields
- **WHEN** `contact_search` returns a list of contacts
- **THEN** each contact in the response MUST include `dunbar_tier` and `dunbar_score`

---

### Requirement: Entity list sorted by role priority then Dunbar score
The entities page (`/butlers/entities`) SHALL sort person-entities by role priority first, then by Dunbar decay score within each role group.

#### Scenario: Role-based primary sort order
- **WHEN** the entities page loads
- **THEN** person-entities MUST be sorted with role priority: `owner` first, then `family`, then any other manually assigned roles (alphabetically), then entities with no roles
- **AND** within each role group, entities MUST be sorted by Dunbar decay score descending

#### Scenario: Entity with multiple roles uses highest-priority role
- **WHEN** an entity has roles `["family", "colleague"]`
- **THEN** the entity MUST be sorted using its highest-priority role (`family`)

#### Scenario: Non-person entities sorted after all person-entities
- **WHEN** the entities page displays a mix of person, organization, and place entities
- **THEN** person-entities MUST appear first (sorted by role then score)
- **AND** non-person entities MUST appear after, sorted by `canonical_name` alphabetically

#### Scenario: Dunbar score available in entity API response
- **WHEN** `GET /api/memory/entities` returns person-entities
- **THEN** each person-entity MUST include `dunbar_tier` (integer) and `dunbar_score` (float) fields
- **AND** these fields MUST be NULL for non-person entities

---

### Requirement: Entity list API supports Dunbar sort order
The entities API SHALL support sorting by role priority and Dunbar score.

#### Scenario: Default sort includes Dunbar ranking
- **WHEN** `GET /api/memory/entities` is called without explicit sort parameters
- **THEN** person-entities MUST be returned sorted by role priority descending, then Dunbar score descending
- **AND** the response model MUST include `dunbar_tier` and `dunbar_score` fields in `EntitySummary`

#### Scenario: Search results preserve Dunbar sort
- **WHEN** `GET /api/memory/entities?q=alice` is called with a search query
- **THEN** matching entities MUST be sorted by role priority then Dunbar score (not alphabetically)

---

### Requirement: Concentric circles Dunbar visualization
The entities page SHALL provide a "Concentric Circles" button that opens a dialog visualizing the user's social network as Dunbar layers radiating outward from the center.

#### Scenario: Button placement and label
- **WHEN** the entities page loads
- **THEN** a "Concentric Circles" button MUST be visible in the page header area alongside existing controls
- **AND** the button MUST use an icon suggesting radiating circles or a target/bullseye

#### Scenario: Dialog layout with concentric rings
- **WHEN** the user clicks the "Concentric Circles" button
- **THEN** a dialog MUST open displaying concentric rings, one per populated Dunbar tier
- **AND** the innermost ring MUST represent tier 5 (support clique) with the user/owner at the center
- **AND** successive rings MUST represent tiers 15, 50, 150, 500 radiating outward
- **AND** tier 1500 MUST be represented as the outermost region or omitted if empty

#### Scenario: People displayed within their tier ring — progressive detail
- **WHEN** the concentric circles dialog is open
- **THEN** tier 5 and tier 15 entities MUST appear as labeled nodes with avatar (or initials fallback) and full name
- **AND** tier 50 entities MUST appear as labeled nodes with initials and name on hover
- **AND** tiers 150, 500, and 1500 MUST display a count badge per tier with the top 5 names listed and a "show all" expansion
- **AND** entities with manual tier overrides MUST be visually distinguished (e.g., a pin icon or border accent)

#### Scenario: Tier ring labels
- **WHEN** the concentric circles dialog is open
- **THEN** each ring MUST be labeled with the tier name and count (e.g., "Support Clique (4)", "Sympathy Group (12)")
- **AND** empty tiers MAY be rendered as thin unlabeled rings or omitted

#### Scenario: Click-through to entity detail
- **WHEN** the user clicks on a person node in the concentric circles visualization
- **THEN** the dialog MUST navigate to that entity's detail page at `/entities/:entityId`

#### Scenario: Responsive sizing
- **WHEN** the dialog is displayed
- **THEN** the visualization MUST scale to fit the dialog dimensions
- **AND** inner tiers with fewer people MUST have proportionally smaller rings
- **AND** outer tiers with more people MUST have proportionally larger rings to accommodate labels

---

### Requirement: Cold start behavior
The system SHALL provide meaningful defaults when insufficient interaction data exists for Dunbar tier computation.

#### Scenario: No interaction data — entity list fallback
- **WHEN** the entities page loads and fewer than 5 contacts have any interaction history
- **THEN** the entity list MUST fall back to sorting by role priority then `canonical_name` alphabetically
- **AND** `dunbar_tier` and `dunbar_score` MUST still be returned (as 1500 and 0.0 respectively for unscored contacts)

#### Scenario: No interaction data — concentric circles empty state
- **WHEN** the user opens the concentric circles dialog and fewer than 5 contacts have any interaction history
- **THEN** the dialog MUST display an empty state message: "Interact with your contacts to see your social map take shape"
- **AND** the owner entity MUST still appear at the center
- **AND** any contacts with manual tier overrides MUST appear in their assigned rings

#### Scenario: Gradual calibration
- **WHEN** the system has partial interaction data (some contacts scored, many not)
- **THEN** scored contacts MUST be ranked normally among themselves
- **AND** unscored contacts MUST be assigned tier 1500 and appear below scored contacts in all rankings
