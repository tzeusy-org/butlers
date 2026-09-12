## ADDED Requirements

### Requirement: Snapshot-backed Bead detail endpoint

The dashboard API SHALL expose additive `GET /api/beads/{id}` as
`ApiResponse<BeadDetail>`, using only the bounded shared snapshot reader. A
successful response SHALL include `meta.export_as_of`, the mounted export
mtime, and a `data` object with only: `id`, `title`, `status`, `priority`,
`type`, `description`, `design`, `acceptance_criteria`, `labels`,
`created_at`, `updated_at`, `started_at`, `closed_at`, `due_at`, bounded
`dependencies`, and `external_ref`. A dependency summary SHALL contain only
`id`, `title`, `status`, `priority`, and `type`; it SHALL be limited to the
first 20 direct dependencies in source order.

The endpoint MUST NOT return notes, metadata, comments, identities,
credentials, raw records, raw dependency edges, arbitrary hrefs, or fields
not listed above. It MUST NOT call `bd`, Dolt, GitHub, a database, an external
service, or a tracker mutation path.

When the snapshot is readable and sufficiently fresh but lacks the requested
ID, the endpoint SHALL return HTTP 404 `ErrorResponse` with code
`BEAD_NOT_FOUND`. When the snapshot is missing, stale, oversized, unreadable,
or malformed, it SHALL return HTTP 503 `ErrorResponse` with code
`BEAD_SNAPSHOT_UNAVAILABLE`; `error.details.export_as_of` SHALL be present as
an ISO timestamp or `null`. It MUST NOT return 404 until availability has been
established.

#### Scenario: Fresh matching record returns a bounded allowlist

- **WHEN** a readable export newer than the freshness limit contains the
  requested ID
- **THEN** `GET /api/beads/{id}` returns HTTP 200 with an `ApiResponse`
  containing only the specified safe fields
- **AND** `meta.export_as_of` equals the export mtime
- **AND** dependency summaries contain no more than 20 source-order direct
  records

#### Scenario: Fresh snapshot distinguishes a missing ID

- **WHEN** a readable sufficiently fresh export does not contain the
  requested ID
- **THEN** `GET /api/beads/{id}` returns HTTP 404 with code
  `BEAD_NOT_FOUND`

#### Scenario: Unavailable snapshot never becomes not found

- **WHEN** the export is missing, stale, oversized, unreadable, or malformed
- **THEN** `GET /api/beads/{id}` returns HTTP 503 with code
  `BEAD_SNAPSHOT_UNAVAILABLE`
- **AND** `error.details.export_as_of` is present even when its value is null
- **AND** the endpoint does not return HTTP 404 or an empty success payload
