## RENAMED Requirements

- FROM: `### Requirement: [TARGET-STATE] Switchboard insight reader endpoint`
- TO: `### Requirement: Switchboard insight reader endpoint`

## MODIFIED Requirements

### Requirement: Attention Daily Rollup
The system SHALL persist a durable daily rollup of owner-engagement signal in `public.attention_daily_rollup`, so the disengagement ratchet's history survives the 30-day `insight_engagement` purge and cannot be poisoned by non-owner ingress.

#### Scenario: Rollup schema
- **WHEN** the `public.attention_daily_rollup` table is created
- **THEN** it SHALL include: `day` (DATE, primary key), `owner_ingress_count` (INTEGER, default 0), `insights_delivered` (INTEGER, default 0), `insights_engaged` (INTEGER, default 0), `updated_at` (TIMESTAMPTZ, auto-updated)

#### Scenario: Owner ingress recorded daily
- **WHEN** the Switchboard's engagement gate resolves an ingress sender to the owner
- **THEN** it SHALL upsert the current UTC day's `owner_ingress_count` in `public.attention_daily_rollup`, incrementing it by 1
- **AND** a rollup-write failure SHALL NOT block ingress routing

#### Scenario: Insight delivery rolled up before purge
- **WHEN** the delivery cycle's cleanup step purges `public.insight_engagement` rows older than 30 days
- **THEN** it SHALL first upsert each affected day's delivered/engaged counts into `public.attention_daily_rollup`

### Requirement: Switchboard insight reader endpoint

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
- **AND** no grant migration MUST extend SELECT on that table to the health or dashboard role

#### Scenario: Status filter defaults to pending

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health` with no `status` parameter
- **THEN** only candidates with `status = 'pending'` MUST be returned

#### Scenario: Butler filter scopes the result

- **WHEN** the reader is called with `butler=health`
- **THEN** candidates whose `origin_butler` is not `health` MUST NOT appear in the result
