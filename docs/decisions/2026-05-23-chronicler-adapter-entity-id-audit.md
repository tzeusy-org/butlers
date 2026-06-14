# Chronicler Adapter entity_id Audit

> **SUPERSEDED (2026-06-14):** This audit is historical and no longer reflects the
> schema. The `episodes.entity_id` column it analyzes was **dropped** in bu-cfsgy
> (PR #2292), and the calendar-specific backfill script it references
> (`scripts/backfill_episode_entity_id.py`) has been **removed** from the repo.
> Entity attribution now lives solely in the `episode_entities` join table. Retained
> for decision-history context; do not treat its `episodes.entity_id` /
> backfill-script guidance as actionable.

**Date:** 2026-05-23
**Issue:** bu-q7hf6
**Reference implementation:** [`src/butlers/chronicler/adapters/calendar.py`](../../src/butlers/chronicler/adapters/calendar.py)

## Context

The calendar adapter (bu-f4755) introduced `entity_id` population on `chronicler.episodes` rows, linking each episode to the owner entity from the memory butler's entity graph. The pattern: resolve the entity once per schema (or once per logical source) and stamp it on every projected row. A backfill script handles historical rows.

Nine remaining adapters have not yet been evaluated for this treatment. This document audits each one and records a decision.

---

## Scope clarification (calendar reference pattern)

`calendar.py` populates two complementary entity-id mechanisms:

1. **`episodes.entity_id` column** — the single primary entity (the owner of the schema/account); resolved via `_resolve_schema_entity_id` and written on every upsert.
2. **`episode_entities` join table** — full entity attribution including the owner. Per `calendar.py` lines 481–489, **the owner is always written to `episode_entities` with `role='owner'`** for every episode, not only when other participants exist. For calendar events with attendees, additional rows with `role='participant'` are also written.

The owner-only adapters audited below must port **both mechanisms** to match the calendar reference and to remain forward-compatible with the planned drop of `episodes.entity_id` (bu-cfsgy, after two release cycles). Concretely:
- Populate `episodes.entity_id` = owner on every upserted row.
- Write a single row into `episode_entities` per episode: `(episode_id, owner_entity_id, role='owner')`.
- Do **not** call `_fetch_event_entities` or any multi-participant resolution — owner-only adapters have no participants to enumerate.

The simpler subset (owner-only, no participants) is still a port of the same pattern, just without the attendee-resolution branch.

---

## Summary Table

| Adapter | Episode/Event type | Meaningful? | Resolves to | Follow-up needed |
|---|---|---|---|---|
| `focus` (FocusInferredAdapter) | `focus_block` (derived) | Yes | Owner | Yes |
| `sessions` (CoreSessionsAdapter) | `work` episode + point events | Yes | Owner | Yes |
| `spotify` (SpotifySessionAdapter) | `listening_episode` | Yes | Owner | Yes |
| `steam` (SteamPlayAdapter) | `play_episode` | Yes | Owner | Yes |
| `meals` (MealsAdapter) | `eating_event` point event | Yes | Owner | Yes |
| `owntracks` (OwnTracksPointAdapter) | `location` + `movement_episode` | Yes | Owner | Yes |
| `reading` (ReadingInferredAdapter) | `reading_block` (derived) | Yes | Owner | Yes |
| `google_health` (Sleep/Workout) | `sleep_episode`, `workout_episode` | Yes | Owner | Yes |
| `google_health` (Steps) | `daily_steps` point events | Yes | Owner | Yes — distinct `source_name = 'health.steps'` |
| `google_health` (HeartRate) | `heart_rate_summary` point events | Yes | Owner | Yes — distinct `source_name = 'health.heart_rate'` |
| `home_assistant` (HomeAssistantHistoryAdapter) | `presence_episode` | Conditional | Named person entity | Yes — requires entity resolution design |

---

## Per-Adapter Decisions

### focus — FocusInferredAdapter

**Meaningful: Yes.** Focus blocks are inferred from owner-driven activity: long task sessions the owner ran, or calendar events the owner explicitly titled as focus/deep work/pomodoro. Every `focus_block` episode records what the owner was doing. `entity_id` = owner.

**Implementation sketch:** The adapter reads from `chronicler.episodes` (not a butler schema), so there is no per-schema entity lookup. The owner entity_id should be resolved once at the start of the `project()` call (consistent with the architectural pattern of providing database access during projection, not at construction) from `public.contacts WHERE 'owner' = ANY(roles)` joined to the entity graph, and stamped on every upserted episode. No per-row resolution needed.

**Alternative:** inherit entity_id from the source episode (more robust to future multi-entity sources, but no behavior difference in single-owner v1). v1 should use owner-resolution for consistency with the other owner-only adapters; revisit if upstream adapters ever produce non-owner entity_id.

---

### sessions — CoreSessionsAdapter

**Meaningful: Yes.** Session episodes describe the owner's agent/butler interactions — "owner ran a task session." For route-triggered sessions (trigger_source='route'), the episode already resolves a contact display name; the episode subject is still the owner running the session, not the contact. `entity_id` = owner.

**Implementation sketch:** Same single-lookup pattern as focus: resolve the owner entity_id once per adapter run (not per schema, since it does not depend on which schema the session came from) and pass it into `_project_row`. The `work` episode and both point events (`session_started`, `session_completed`) should all carry `entity_id`.

---

### spotify — SpotifySessionAdapter

**Meaningful: Yes.** Spotify listening sessions are an owner-action: the owner listened to music. The evidence table stores `spotify_user_id` which could theoretically map to a non-owner account, but in practice the Spotify connector is configured for a single user (the owner). `entity_id` = owner.

**Conditional note:** If the deployment ever tracks multiple Spotify users, `spotify_user_id` → entity resolution would be needed. For v1, treating all sessions as owner-initiated is correct and consistent with how the calendar adapter treats all schemas as belonging to the same owner.

**Implementation sketch:** Resolve owner entity_id once at run start, stamp on every `_project_row` call. If multi-user support is later added, a `public.spotify_accounts` lookup table (analogous to `public.google_accounts`) would be needed.

---

### steam — SteamPlayAdapter

**Meaningful: Yes.** Steam play-history records the owner gaming. The `steam_account_id` column exists in the evidence table and could in principle map to an entity, but the connector is single-user (owner-only). `entity_id` = owner.

**Implementation sketch:** Resolve owner entity_id once at run start, stamp on every `_project_row` call. Same pattern as Spotify.

---

### meals — MealsAdapter

**Meaningful: Yes.** Meals are explicitly personal self-tracking data: the owner ate a meal. The episode is a point event, not an episode, but the `entity_id` column exists on `point_events` as well. `entity_id` = owner.

**Implementation sketch:** Resolve owner entity_id once at run start, stamp on every `_project_row` call. The `PointEvent` model needs to be verified that it accepts `entity_id` (mirrors the check needed for all point-event adapters).

---

### owntracks — OwnTracksPointAdapter

**Meaningful: Yes.** OwnTracks location points are the owner's physical position. The `endpoint_identity` field identifies which device/user uploaded the data, but in a single-owner deployment this is always the owner. `entity_id` = owner.

**Conditional note:** If multiple household members use OwnTracks under the same butler instance, `endpoint_identity` → entity resolution would give each person's movement their own entity. This is a natural extension but requires an `endpoint_identity → entity_id` mapping table. For v1, owner-only is correct.

**Implementation sketch:** Resolve owner entity_id once at run start. Stamp on both `location` point events and `movement_episode` episodes in `_project_point_event` and `_project_movement_episodes`.

---

### reading — ReadingInferredAdapter

**Meaningful: Yes.** Reading blocks are inferred from the owner's calendar and health facts — the owner read something. Same argument as focus. `entity_id` = owner.

**Implementation sketch:** Resolve owner entity_id once at run start, stamp on both calendar-derived and fact-derived episodes in `_project_calendar_row` and `_project_fact_row`.

**Alternative:** inherit entity_id from the source episode (more robust to future multi-entity sources, but no behavior difference in single-owner v1). v1 should use owner-resolution for consistency with the other owner-only adapters; revisit if upstream adapters ever produce non-owner entity_id.

---

### google_health — GoogleHealthSleepAdapter, GoogleHealthWorkoutAdapter, GoogleHealthStepsAdapter, GoogleHealthHeartRateAdapter

**Meaningful: Yes.** All four adapters project the owner's biometric and physical activity data. Sleep, workouts, steps, and heart rate are self-monitoring signals. `entity_id` = owner.

**Note on multiple adapters and distinct source_names:** `google_health.py` contains four adapter classes, but they do **not** all share the same `source_name`. The mapping is:

| Class | `SOURCE_NAME` value |
|---|---|
| `GoogleHealthSleepAdapter` | `google_health.measurements` |
| `GoogleHealthWorkoutAdapter` | `google_health.measurements` |
| `GoogleHealthStepsAdapter` | `health.steps` |
| `GoogleHealthHeartRateAdapter` | `health.heart_rate` |

Steps and heart-rate adapters use distinct `source_name` values (`health.steps` and `health.heart_rate`) that are different from the sleep/workout pair. **A backfill that only filters on `source_name = 'google_health.measurements'` will silently skip historical steps and heart-rate point events.** The backfill must enumerate all three source_name values: `google_health.measurements`, `health.steps`, and `health.heart_rate`.

Each adapter class needs the owner entity_id lookup independently (they each have separate `project()` entrypoints), or a shared helper could be introduced.

**Implementation sketch:** Resolve owner entity_id once per `project()` call for each adapter class. Stamp on all `sleep_episode`, `workout_episode`, and point event rows. The `_facts_table` and predicate queries are already shared via `_fetch_fact_rows`; the `entity_id` parameter can flow into each `_project_row` call.

---

### home_assistant — HomeAssistantHistoryAdapter

**Meaningful: Conditional.** Home Assistant presence episodes (`person.*` entities) describe a named person being at home. For a single-person household this is always the owner; for a multi-person household (e.g. `person.alice`, `person.bob`) each entity represents a different resident who may or may not have an entity in the memory butler's graph.

The episode title already reflects the entity: `"Alice at home"`. Stamping `entity_id` here is more nuanced than any other adapter because the person being tracked is encoded in `entity_id` (the HA entity ID like `person.alice`), not the owner.

**Why not straightforward owner stamping:** Stamping the owner entity on `person.bob`'s presence episode would be semantically incorrect. The entity_id should resolve to the person whose presence is being tracked.

**Recommended approach (for the follow-up bead):**
1. Introduce a mapping from HA `person.entity_id` (e.g. `person.alice`) to `public.entities.id`. This could live in `public.contact_info` with `type='home_assistant_person'` or in a dedicated `connectors.home_assistant_entities` table.
2. In `_project_presence_episodes`, resolve the entity_id for each `person.*` entity once per entity (not per row) before the rollup loop. Degrade to NULL when no mapping exists.
3. Owner households where only `person.owner` exists can map trivially; multi-resident households get per-person attribution.

---

## Closing: Follow-up Beads for the Coordinator

The following implementation beads should be created. All follow the same calendar pattern: resolve entity_id once (at run start or once per logical source), pass it through `_project_row`, and address backfill separately (see note below).

---

### 1. Port entity_id to focus, sessions, spotify, steam, meals, owntracks, reading, google_health adapters (owner-only)

**Title:** `feat(chronicler): port entity_id population to owner-only adapters (focus, sessions, spotify, steam, meals, owntracks, reading, google_health)`

**Description:** Eight adapters produce episodes/events for owner-driven data but do not populate `entity_id`. Each should resolve the owner entity_id once per `project()` call (from `public.contacts WHERE 'owner' = ANY(roles)`) and stamp it on every upserted row. Pattern mirrors the calendar adapter's `_resolve_schema_entity_id`.

**Backfill note:** The existing `scripts/backfill_episode_entity_id.py` is calendar-specific and **cannot be extended by simple parameterization**. It is hardwired to `google_calendar.completed` via `_SUPPORTED_ADAPTERS = frozenset({"google_calendar.completed"})` — the CLI `--adapter` flag only accepts values from that frozenset. Its resolution path (`{schema}.calendar_sources.metadata->>'account_email'` → `public.google_accounts.entity_id`) is calendar-specific and does not apply to owner-only adapters.

The owner-only adapters use a different and simpler resolution: `SELECT entity_id FROM public.contacts WHERE 'owner' = ANY(roles)`. This requires either:
- **(Recommended)** A new sibling script `scripts/backfill_owner_episode_entity_id.py` that targets the owner-only source names and uses the `public.contacts` owner lookup, OR
- A refactor of the existing script to accept multiple resolution strategies (substantially more complex).

**Source-name enumeration.** The eight adapter groups in this bead emit **ten distinct `source_name` values** (the backfill must cover all ten to avoid silent data gaps):

1. `focus.inferred` (FocusInferredAdapter)
2. `core.sessions` (CoreSessionsAdapter)
3. `spotify.session` (SpotifySessionAdapter)
4. `steam.play` (SteamPlayAdapter)
5. `meals.events` (MealsAdapter)
6. `owntracks.locations` (OwnTracksPointAdapter)
7. `reading.inferred` (ReadingInferredAdapter)
8. `google_health.measurements` (GoogleHealthSleepAdapter + GoogleHealthWorkoutAdapter — shared)
9. `health.steps` (GoogleHealthStepsAdapter)
10. `health.heart_rate` (GoogleHealthHeartRateAdapter)

(Implementer: verify each `SOURCE_NAME` constant in the corresponding adapter file before wiring the backfill — the list above is the source of truth at the time of writing but should be re-checked.)

This decision is deferred to bu-4c1ks; the implementing agent must choose and document the approach before writing code. Tests: unit tests mocking the owner lookup + an integration test asserting the field is non-null on new projections.

---

### 2. Design entity resolution for Home Assistant presence episodes (multi-person)

**Title:** `feat(chronicler): home_assistant adapter entity_id — design + implement person-entity resolution`

**Description:** The Home Assistant adapter tracks per-person presence (`person.alice`, `person.bob`). Unlike other adapters, the entity_id should reflect the person being tracked, not necessarily the owner. This bead should: (a) decide where the `person.*` → `public.entities.id` mapping lives (likely `public.contact_info` with a new type or a dedicated table), (b) implement the lookup in `_project_presence_episodes` with owner fallback for single-person deployments, and (c) document the mapping bootstrap process. Degrade to NULL when no mapping is found. Update the backfill for `home_assistant.history` presence episodes separately (owner-lookup does not apply here).
