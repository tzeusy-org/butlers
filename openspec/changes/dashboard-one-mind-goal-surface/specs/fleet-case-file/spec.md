## ADDED Requirements

### Requirement: Contributor filter on the case list

After the signed D19 design and the existing program M6 cutover, `GET /api/switchboard/cases` SHALL accept an optional `contributor` query parameter naming one butler. When present, the server SHALL return only cases that have at least one evidence row whose stored `contributor` equals that name, applying the filter in the database query together with any state and posture filters. The response SHALL keep the read surface's existing contract: updated_at/id descending keyset order, limit 1..200 with default 50, `next_cursor`/`has_more`, the honest source-availability classification, and read-only behavior. Links alone SHALL NOT make a case match. The filter SHALL add no write, no new availability DTO and no change to the detail endpoint. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-fleet-case-file-015
Source: RFC 0032 §Slice plan S2; REQ-fleet-case-file-004 (attributed evidence contributor) and REQ-fleet-case-file-007; RFC 0038 D19; REQ-dashboard-regions-005; bu-85bqeq.1
Scope: post-v1

#### Scenario: Filter by contributor

- **WHEN** a list request carries `contributor=<butler>`
- **THEN** only cases with at least one evidence row from that contributor are returned, in the same descending keyset order and page limits as an unfiltered request.

#### Scenario: Combined with state and posture

- **WHEN** a list request carries a contributor filter together with state or posture filters
- **THEN** a case is returned only if it matches every filter, and the cursor continues that same filtered set.

#### Scenario: No contributions

- **WHEN** the named contributor has no evidence on any case
- **THEN** a healthy empty page is returned; it is never reported as source unavailability.

#### Scenario: Malformed contributor

- **WHEN** the `contributor` value is blank after trimming
- **THEN** the request is refused with the read surface's existing 422 validation response and no case query runs.

#### Scenario: Source failure stays honest

- **WHEN** the case source fails during a filtered request
- **THEN** the response reports unavailability under the same classification as an unfiltered request, never an empty page.
