## MODIFIED Requirements

### Requirement: Calendar Event Full-Text Search Query

The module SHALL expose a fan-out search over the `calendar_events` projection that matches a free-text query against `title`, `description`, and `location`, returns matches ranked by trigram relevance with each match's date(s), and degrades fail-open when the trigram index or extension is unavailable. This is the contract behind the `GET /api/calendar/workspace/search` endpoint (see `dashboard-api`). The result envelope SHALL carry an `available` boolean that is `false` only when EVERY targeted schema fails to respond, so callers can distinguish "nothing matched" from "search could not run".

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

#### Scenario: available signal is false only when all targeted schemas fail
- **WHEN** the search fan-out completes across all targeted butler schemas
- **THEN** the result envelope's `available` flag is `false` if and only if every targeted schema failed to respond (pool error, or both trigram and ILIKE queries raised for that schema)
- **AND** `available` is `true` whenever at least one schema responded successfully — even if it returned an empty match set or fell back to ILIKE
- **BECAUSE** `available=false` means "the search could not run" (caller should show "search unavailable"), whereas an empty `matches` with `available=true` means "the search ran and found nothing"
