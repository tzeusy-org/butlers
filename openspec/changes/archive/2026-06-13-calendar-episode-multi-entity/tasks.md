## 1. Spec Authoring

- [x] 1.1 Create `openspec/changes/calendar-episode-multi-entity/` with
      the default spec-driven OpenSpec schema.
- [x] 1.2 Write `proposal.md` recommending Option A (join table) with
      defended rationale citing `butler-chronicler` idempotency invariant
      and RFC 0014 §D1.
- [x] 1.3 Write `design.md` with doctrine alignment, schema sketch,
      adapter behavior, idempotency story, entity-merge re-pointing,
      API contract delta, dashboard impact, cut-over sequencing against
      bu-uhjxr, backfill story, telemetry, and risks.
- [x] 1.4 Write `butler-chronicler` delta spec for the multi-entity
      storage shape and replay rule.
- [x] 1.5 Write `chronicler-api` delta spec for the additive
      `participant_entity_ids` field and `participant_entity_id` filter.
- [x] 1.6 Write `module-calendar` delta spec clarifying that chronicler
      reads from `calendar_event_entities` and the calendar module is
      the source of truth for attendee → entity resolution.
- [x] 1.7 Run `openspec validate calendar-episode-multi-entity --strict`
      and confirm it passes.

## 2. Schema Migration (downstream — `chronicler_014`)

- [ ] 2.1 Create Alembic migration `chronicler_014_episode_entities.py`:
      - CREATE TABLE `chronicler.episode_entities` with the schema in
        design D1.
      - CREATE INDEX `episode_entities_entity_idx`.
      - Recreate `v_episodes_corrected` to add aggregated
        `participant_entity_ids UUID[]` column. The aggregation MUST
        return `'{}'::uuid[]` (never NULL) for episodes with no
        participants, and the array order MUST match the API contract
        (role precedence `'owner' > 'organizer' > 'participant'`,
        `entity_id ASC` tiebreak). Use:
        `COALESCE(array_agg(entity_id ORDER BY CASE role WHEN 'owner'
        THEN 0 WHEN 'organizer' THEN 1 ELSE 2 END, entity_id)
        FILTER (WHERE entity_id IS NOT NULL), '{}')`
      - Downgrade: drop view, drop index, drop table; restore prior
        `v_episodes_corrected`.
- [ ] 2.2 Write a migration smoke test that asserts: table exists,
      index exists, view has the new column, downgrade is clean.
- [ ] 2.3 Update `roster/chronicler/AGENTS.md` migration notes section
      with the new migration's pre- and post-conditions.

## 3. Adapter Implementation (downstream)

- [ ] 3.1 Extend `CalendarCompletedAdapter._fetch_instances` (or add a
      sibling helper) to load the `calendar_event_entities` join for
      each schema in batch (one query per schema, not per row).
- [ ] 3.2 Add `_upsert_episode_entities(conn, episode_id, owner_id,
      participant_ids)` that runs DELETE + INSERT in a single
      transaction.
- [ ] 3.3 Update `_project_row` to call `_upsert_episode_entities`
      after the canonical episode upsert.
- [ ] 3.4 Preserve `episodes.entity_id` write as the derived owner
      column for the transition window.
- [ ] 3.5 Update `episode_entities.schema_absent` debug log when the
      upstream join is missing; do not raise.
- [ ] 3.6 Add unit tests:
      - Owner-only when join table absent.
      - Owner + participants when join table present.
      - DELETE-then-INSERT replaces stale attendees on second adapter
        run.
      - Idempotent replay does not duplicate `episode_entities` rows.
      - `episodes.entity_id` equals the `'owner'` row in
        `episode_entities` when both are written.

## 4. API Surface Changes (downstream)

- [ ] 4.1 Update `chronicler.storage.list_episodes` to accept
      `participant_entity_id: UUID | None` parameter; join against
      `episode_entities` when supplied.
- [ ] 4.2 Update `chronicler.storage` row → model helpers to expose
      `participant_entity_ids` from the recreated `v_episodes_corrected`.
- [ ] 4.3 Update `/api/chronicler/episodes` handler to accept
      `participant_entity_id` query param; preserve the existing
      `entity_id` (owner-only) param for backward compat.
- [ ] 4.4 Update `/api/chronicler/episodes/{id}` response to include
      `participant_entity_ids`.
- [ ] 4.5 Update the corrected-view-only guardrail test to recognize
      the new `participant_entity_ids` column in
      `v_episodes_corrected`.
- [ ] 4.6 Add API tests asserting the new filter and the new field on
      single-episode and list responses.

## 5. Entity-Activity Aggregator Switch

- [ ] 5.1 In `roster/relationship/api/router.py`, update the
      `GET /api/butlers/relationship/entities/{id}/activity` handler
      to call `chronicler_list_episodes(participant_entity_id=id)`
      instead of `entity_id=id`. The owner column remains a valid
      filter; the join is now authoritative.
- [ ] 5.2 Add an aggregator test asserting that a meeting episode where
      the requested entity is a participant (not the owner) surfaces in
      the activity feed.

## 6. Memory Entity Merge Re-Pointing

- [ ] 6.1 Add `_repoint_episode_entities(pool, src_uuid, tgt_uuid)` in
      `src/butlers/modules/memory/tools/entities.py`, mirroring the
      existing `_repoint_calendar_event_entities` helper.
- [ ] 6.2 Wire the re-pointer into `entity_merge()` next to the
      existing calendar repointing call.
- [ ] 6.3 Also update `chronicler.episodes.entity_id` rows that equal
      the source UUID during the transition window.
- [ ] 6.4 Add merge tests asserting both the join table and the
      derived owner column move atomically.

## 7. Backfill (separate bead, optional ship-blocker)

- [ ] 7.1 Write `scripts/backfill_episode_participants.py` analogous
      to `scripts/backfill_episode_entity_id.py`:
      - Walks every `chronicler.episodes` row with
        `source_name='google_calendar.completed'`.
      - For each, derives `(schema, origin_instance_ref)` from
        `payload`.
      - Reads the upstream attendee set from
        `{schema}.calendar_event_entities` joined through
        `calendar_events.id`.
      - Upserts the `episode_entities` set.
      - Idempotent; supports `--dry-run`.
- [ ] 7.2 Document the watermark-reset alternative in
      `roster/chronicler/AGENTS.md` (mirrors the existing entity_id
      backfill section).
- [ ] 7.3 Add a script test asserting idempotent re-run does not write
      duplicate rows.

## 8. Telemetry and Observability

- [ ] 8.1 Add counter
      `chronicler_episode_participants_resolved_total{schema}` to the
      adapter run loop.
- [ ] 8.2 Add OTel span attribute
      `chronicler.episodes.filter_kind=participant_join` on
      `/api/chronicler/episodes` requests that supply
      `participant_entity_id`.
- [ ] 8.3 Add a Grafana panel proposal note (no dashboard change in
      this change; a follow-up bead can wire the panel).

## 9. Cleanup (follow-up bead, two release cycles later)

- [ ] 9.1 Migration `chronicler_0xx_drop_episodes_entity_id.py`: drop
      the derived `episodes.entity_id` column, recreate
      `v_episodes_corrected` without it.
- [ ] 9.2 Remove the now-dead single-column filter path from the
      storage and API layers.
- [ ] 9.3 Remove the derived-owner write from the adapter.
- [ ] 9.4 Remove the legacy `entity_id` query param from the API or
      alias it to `participant_entity_id` with role=owner filter.

## 10. Beads Graph Decomposition (coordinator responsibility, NOT this bead)

- [ ] 10.1 After this change is approved and merged, the coordinator
      decomposes the downstream work above into acyclic implementation
      beads via `/beads-writer`.
- [ ] 10.2 The downstream beads SHALL link `discovered-from: bu-j6rqm`
      so the original implementation bead's traceability survives.
- [ ] 10.3 The coordinator SHALL close `bu-j6rqm` as
      "superseded by approved design + decomposed beads" at the same
      time the new beads are filed.

> **Note**: This bead (bu-ea6c7) is spec authoring only. Worker MUST
> NOT call `/beads-writer` or `bd create` for the implementation graph.
> That is the coordinator's call post-merge.
