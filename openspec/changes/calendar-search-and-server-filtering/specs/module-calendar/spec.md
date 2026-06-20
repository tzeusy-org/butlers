## ADDED Requirements

### Requirement: Calendar Event Full-Text Search Index

The calendar projection SHALL support index-backed substring search over the human-readable event text. A core Alembic migration (next in the `core_*` chain) SHALL ensure the `pg_trgm` extension and create a GIN trigram index over `calendar_events(title, description, location)` in each butler schema, so free-text lookups do not require a sequential scan of the projection.

#### Scenario: Trigram index migration is idempotent and reversible
- **WHEN** the search-index core migration runs against a butler schema
- **THEN** it executes `CREATE EXTENSION IF NOT EXISTS pg_trgm` and creates a GIN trigram index (`gin_trgm_ops`) over `calendar_events(title, description, location)` with `IF NOT EXISTS`
- **AND** re-running the migration is a no-op (no duplicate index, no error)
- **AND** the migration `downgrade()` drops the index (`DROP INDEX IF EXISTS`) while leaving the shared `pg_trgm` extension installed

#### Scenario: Search index covers the searchable projection columns
- **WHEN** the projection stores a `calendar_events` row with `title`, optional `description`, and optional `location`
- **THEN** all three columns are covered by the trigram index so a substring query against any of them is index-eligible
- **AND** the index is per-schema, consistent with the projection's per-butler-schema layout

### Requirement: Calendar Event Full-Text Search Query

The module SHALL expose a fan-out search over the `calendar_events` projection that matches a free-text query against `title`, `description`, and `location`, returns matches ranked by trigram relevance with each match's date(s), and degrades fail-open when the trigram index or extension is unavailable. This is the contract behind the `GET /api/calendar/workspace/search` endpoint (see `dashboard-api`).

#### Scenario: Ranked match across title, description, and location
- **WHEN** a non-empty query is searched against the projection
- **THEN** `calendar_events` rows whose `title`, `description`, or `location` match the query (trigram similarity / substring) are returned
- **AND** results are ranked by trigram relevance and carry each match's event date(s) so callers can group by day and jump-to
- **AND** the search is fanned out across butler schemas and honors lane (`view`) and `butlers`/`sources` scoping

#### Scenario: Empty query returns no matches
- **WHEN** the search is invoked with a missing or blank query string
- **THEN** an empty result set is returned (the search SHALL NOT return the entire projection)
- **AND** no error is raised

#### Scenario: Degraded search when the trigram index is unavailable
- **WHEN** a probed butler schema lacks the `pg_trgm` extension or the trigram index
- **THEN** the search degrades fail-open — it falls back to a substring (`ILIKE`) match for that schema or skips it — rather than raising a 500
- **AND** results from schemas where the index is present are still returned
