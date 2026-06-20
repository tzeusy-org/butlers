# Proactive Insight Engine — Delta for Health Overview Insight Reader

## ADDED Requirements

### Requirement: [TARGET-STATE] Switchboard insight reader endpoint

The Switchboard SHALL expose a read-only insight reader at `GET /api/switchboard/insights` so dashboard surfaces
can render pending insight candidates without each butler needing read access to the cross-butler
`public.insight_candidates` table. The reader is hosted on the **Switchboard** because the insight
broker (Switchboard) role is the only butler role that already holds SELECT on
`public.insight_candidates`. Per `core_010_insight_tables.py`, `butler_switchboard_rw` is granted full
DML (INSERT/UPDATE/DELETE — hence SELECT) on the table, whereas every other butler role (including
`butler_health_rw`) is granted **INSERT only** and has **no SELECT**. There is no blanket "all butlers
may SELECT all public tables" rule — `database-security` grants butler roles SELECT only on public
tables *outside* the write-authorization matrix, and `public.insight_candidates` is *inside* that
matrix. Hosting the reader on the Switchboard therefore requires **no grant migration** and preserves
schema isolation: a non-Switchboard butler does not gain direct SELECT through a new grant.

The reader SHALL accept a `butler` query parameter that filters by `origin_butler`, a `status`
parameter (default `pending`), and a `limit`. It returns the candidate rows the requesting surface is
allowed to see.

#### Scenario: Read pending health candidates

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health&status=pending`
- **THEN** the Switchboard MUST return insight candidates where `origin_butler = 'health'` and
  `status = 'pending'`
- **AND** each returned item MUST include `id`, `category`, `priority`, `message`, `metadata`,
  `created_at`, `status`, and `expires_at`

#### Scenario: Reader is hosted on the role that already holds SELECT

- **WHEN** the insight reader queries `public.insight_candidates`
- **THEN** it MUST run under the Switchboard (insight broker) role, which already holds access to
  that table
- **AND** the change MUST NOT introduce a new grant migration extending SELECT to the health or
  dashboard role

#### Scenario: Status filter defaults to pending

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health` with no `status` parameter
- **THEN** only candidates with `status = 'pending'` MUST be returned

#### Scenario: Butler filter scopes the result

- **WHEN** the reader is called with `butler=health`
- **THEN** candidates whose `origin_butler` is not `health` MUST NOT appear in the result
