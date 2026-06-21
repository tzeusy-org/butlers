## Why

The calendar-cross-domain-overlays change established the prep-rail read as a
**contract-only** requirement: it specified the read shape and honest empty-state
but explicitly did NOT build the data source or authorize a direct cross-butler
read (RFC-0020). The meeting-prep rail (epic `bu-1ajgg9`, bead `bu-jvpfv1`) now
needs the concrete behavior: a deterministic contribution job that precomputes
per-event prep context, a cached cross-schema view to read it from, and the
read endpoint that serves it.

The doctrine constraint is the whole point: the calendar workspace fans out
across ALL calendar-owning butlers, most of which (general/finance/health/
relationship/lifestyle) have NO email module. So prep context — including the
message/email panel — MUST be sourced via the RFC-0010 deterministic
contribution-job path (precomputed into a cached view), NOT a direct cross-schema
SELECT against `relationship.*` / `health.*` / email and NOT a per-open LLM
session. This mirrors the already-merged overlay foundation
(`calendar.v_overlay_contributions` + the per-butler `calendar_overlay_contribution`
jobs) rather than inventing a parallel mechanism.

## What Changes

### Modified capability: `calendar-overlay-aggregation`

- **Meeting-Prep Contribution Schema + State Key Convention.** The relationship
  butler (owner of the entity graph, co-attended edges, and relationship notes)
  writes a structured per-event prep envelope under the key
  `calendar/prep/<event_id>` — the prep analogue of `calendar/overlay/<date>`.
  The envelope carries `attendees` (each with `entity_id`, `name`, `dunbar_tier`
  letter-mark, `notes`, `last_met`/`last_met_event`, and a `message_context`
  slot reserved for email-owning butlers), with no generated prose.
- **Cross-Schema Prep View + Migration.** A migration-tracked read-only SQL view
  `calendar.v_prep_contributions` UNIONs `butler`/`key`/`value` from each
  contributing specialist's `state` table filtered to `key LIKE 'calendar/prep/%'`,
  with a hardcoded `butler` source literal per term and a NULL-stub for absent
  schemas — mirroring `calendar.v_overlay_contributions` (core_140). SELECT
  grants to the calendar reader role are reversible.
- **Relationship Prep Contribution Job.** A `calendar_prep_contribution`
  deterministic (`dispatch_mode="job"`, zero-LLM) job registered in the
  **existing** `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`, scheduled via the
  relationship `butler.toml`.

### Modified capability: `dashboard-api`

- **Meeting-Prep Rail Endpoint.** `GET /api/calendar/workspace/prep/{event_id}`
  projects the cached `calendar.v_prep_contributions` view into the prep-rail
  payload (attendees + notes + last-met, merged across contributing butlers).
  It is a pure read of the cached view — no direct `relationship.*` / `health.*`
  SELECT and no per-open LLM session — and fails open to a structured empty
  payload (never HTTP 500).

## Impact

- **New Alembic migration** `core_142_v_prep_contributions.py` (next in the core
  chain after `core_141`): creates `calendar.v_prep_contributions` and grants
  SELECT on each contributing specialist's `state` table to `butler_calendar_rw`.
  Mirrors `core_140_v_overlay_contributions.py` and reuses its optional-schema
  guard; downgrade drops the view and revokes grants.
- **New contribution job** `src/butlers/jobs/calendar_prep.py`, registered under
  `relationship` in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
  (`src/butlers/scheduled_jobs.py`); `roster/relationship/butler.toml` gains a
  `calendar_prep_contribution` schedule entry (`dispatch_mode="job"`).
- **Read model + API** (`src/butlers/api/read_models/calendar_workspace_v1.py`,
  `src/butlers/api/models/calendar_workspace.py`,
  `src/butlers/api/routers/calendar_workspace.py`): adds `query_calendar_prep` /
  `CalendarPrepRow`, the `CalendarPrep*` response models, and the prep-rail
  endpoint.
- **No change to provider writes and no LLM in the read path.**

## Out of Scope

- **Message/email-context panel population.** The envelope carries a
  `message_context` slot and the read merges it across butlers, but the
  email-owning butlers (messenger/travel) do not yet write prep contributions.
  Populating that panel is a follow-up contribution job, not this change.
- **Rank-based Dunbar tier.** Only the manual `dunbar_tier_override` is surfaced
  (deterministic and cheap); recomputing the full rank-based tier in the prep job
  is out of scope.
- **FE prep-rail rendering.** The frontend rail UI is a separate FE bead; this
  change defines the read contract it consumes.
- **Batched pre-rendered narrative.** Any prose remains deferred (RFC-0020 §Decision).
