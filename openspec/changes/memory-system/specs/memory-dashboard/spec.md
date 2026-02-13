## ADDED Requirements

### Requirement: Memory dashboard API endpoints

The dashboard SHALL expose REST API endpoints for memory data: `GET /api/memory/stats`, `GET /api/memory/facts` (with query params: scope, subject, q, min_confidence), `GET /api/memory/facts/:id`, `PUT /api/memory/facts/:id`, `DELETE /api/memory/facts/:id`, `GET /api/memory/rules` (with query params: scope, maturity, q), `GET /api/memory/rules/:id`, `PUT /api/memory/rules/:id`, `DELETE /api/memory/rules/:id`, `GET /api/memory/episodes` (with query params: butler, from, to), `GET /api/memory/episodes/:id`, `GET /api/memory/activity`. Butler-scoped endpoints SHALL exist at `/api/butlers/:name/memory/{stats,facts,rules,episodes}`. All default API reads SHALL be tenant-bounded; cross-tenant views SHALL require elevated authorization.

#### Scenario: List facts with filters
- **WHEN** `GET /api/memory/facts?scope=health&min_confidence=0.5` is called
- **THEN** the response SHALL contain only facts scoped to 'global' or 'health' with effective_confidence >= 0.5

#### Scenario: Butler-scoped stats
- **WHEN** `GET /api/butlers/health/memory/stats` is called
- **THEN** the response SHALL contain counts scoped to 'global' and 'health' for facts/rules, and butler='health' for episodes

### Requirement: Fact editing via dashboard creates superseding fact

When a fact is edited via `PUT /api/memory/facts/:id`, the system SHALL create a new fact with the updated content that supersedes the original. The original fact's validity SHALL be set to 'superseded'. The edit SHALL go through memory module tool APIs (not direct SQL writes).

#### Scenario: Edit fact creates supersession
- **WHEN** `PUT /api/memory/facts/<id>` is called with `content="Lactose and gluten intolerant"`
- **AND** the original fact had `content="Lactose intolerant"`
- **THEN** a new fact SHALL be created with the updated content
- **AND** the original fact's validity SHALL be 'superseded'

### Requirement: Fact and rule deletion via dashboard is soft-delete

When a fact or rule is deleted via `DELETE /api/memory/facts/:id` or `DELETE /api/memory/rules/:id`, the system SHALL apply canonical soft-delete semantics via memory module tool APIs. Facts SHALL transition to validity `retracted` (legacy `forgotten` accepted only as compatibility alias). Rules SHALL be marked retrieval-excluded tombstones per schema. Records SHALL remain in the database.

#### Scenario: Delete fact via dashboard
- **WHEN** `DELETE /api/memory/facts/<id>` is called
- **THEN** the fact's validity SHALL be `retracted`
- **AND** the fact SHALL no longer appear in retrieval results
- **AND** the fact SHALL remain visible in the dashboard archive

### Requirement: Butler-scoped memory tab

The butler detail page SHALL include a memory tab at `/butlers/:name/memory` with three panels: a facts panel (cards grouped by subject showing content, confidence bar, permanence badge, last confirmed date), a playbook panel (rules grouped by maturity with effectiveness scores), and a collapsible episode stream (chronological with butler badge, importance score, consolidated badge).

#### Scenario: Facts panel displays active facts
- **WHEN** the user navigates to `/butlers/health/memory`
- **THEN** the facts panel SHALL show active facts scoped to 'global' and 'health'
- **AND** each fact SHALL display a color-coded confidence bar (green >0.8, yellow 0.5-0.8, red <0.5)

#### Scenario: Superseded facts shown with strikethrough
- **WHEN** a fact has been superseded
- **THEN** it SHALL appear with strikethrough text and a link to the replacement fact

### Requirement: Cross-butler memory page

A top-level `/memory` page SHALL display: overview cards (total facts by permanence, total rules by maturity, active episodes, fading count), a knowledge browser (unified search across all types with type/scope/permanence filters), a consolidation activity feed (recent fact creations, rule promotions, supersessions, expirations, anti-pattern inversions), and health indicators (confidence distribution chart, episode backlog, rule effectiveness distribution). By default this page aggregates all butlers within the caller tenant.

#### Scenario: Aggregation avoids direct cross-butler DB reads
- **WHEN** the dashboard builds `/memory` aggregates
- **THEN** data SHALL be composed via butler API/tool fanout
- **AND** direct SQL reads into another butler's database SHALL NOT be required

#### Scenario: Overview cards show system-wide counts
- **WHEN** the user navigates to `/memory`
- **THEN** overview cards SHALL show total facts broken down by permanence category
- **AND** total rules broken down by maturity level

#### Scenario: Knowledge browser search
- **WHEN** the user searches "diet" in the knowledge browser
- **THEN** results SHALL include matching facts, rules, and episodes from all butlers in the caller tenant

### Requirement: Memory events in unified timeline

Memory events (fact created, rule promoted, fact expired, anti-pattern inverted) SHALL appear as event types in the unified dashboard timeline alongside session events and other butler activity, sourced from the append-only `memory_events` stream.

#### Scenario: Fact creation appears in timeline
- **WHEN** consolidation creates a new fact
- **THEN** a "fact created" event SHALL appear in the unified timeline with the fact content and source butler

### Requirement: Facts and rules in global search

Facts and rules SHALL be included in the dashboard's global search (Cmd+K). Search results SHALL show the memory type badge, content preview, scope, and confidence/maturity.

#### Scenario: Global search returns facts
- **WHEN** the user presses Cmd+K and searches "allergy"
- **THEN** matching facts SHALL appear in search results alongside other result types
