# Legacy `user_context` census (bu-dhloi1.21)

`public.user_context` (the context bus) retires only when every reader and writer
has a named successor or an explicitly deferred owner, and when shadow/parity
receipts show the successor agrees. An empty search is not evidence. This page is
that census. Nothing here deletes or switches anything; every successor named
below is dark until the RFC 0038 switch (bu-enbrs2).

`tests/contracts/test_user_context_census.py` keeps the guarded tables honest in
both directions: a new reference without a row fails, and a row whose reference
is gone fails. A row key is `path::enclosing function` (`<module>` for a
module-level reference). Docstrings are not references.

## Shadow parity

`butlers.perception.legacy_parity` compares the legacy signals with the
perception reads for the three kinds that replace them:

| Legacy signals | Perception kind | Compared as |
| --- | --- | --- |
| `traveling`, `sleeping`, `meeting`, `focused`, `exercising`, `sick`, `socializing`, `commuting` | `owner.activity` | the set of activity names |
| `at_home`, `in_space`, `away` | `owner.place` | place class only: `inside` (at home, or in a room there) or `outside` |
| `dnd` | `owner.suppression` | on or off |

`compare` is pure and returns one receipt per kind: the legacy and perception
tokens (closed vocabularies only), the perception state, whether they agree, and
a closed reason (`agree`, `value_differs`, `legacy_only`, `perception_only`,
`perception_unknown`, `legacy_conflict`, `unreadable`). A receipt never carries a
room name, place or locality reference, record reference, coordinate, title or
metadata. `LegacyParity.run` reads the legacy side through
`context_bus.get_active_context` on the caller's pool and the perception side as
`perception_owner_reader` on `BUTLERS_PERCEPTION_READER_DSN` with `SET LOCAL
ROLE` inside one transaction, exactly as `/now` does. It returns the receipts and
logs one line of kinds, states, agreement and reasons, with no values. It is
dark: `perception.flags.LEGACY_PARITY_LIVE` is off, nothing calls or schedules
it, and it adds no role or grant.

"At home" in the legacy bus and `inside` in `owner.place` are not the same claim
(`inside` is any known place). A disagreement there is a finding for the switch,
not a bug in either side.

## Readers and writers of `public.user_context`

<!-- census:user_context -->
| Reference | Use | Legacy signals | Successor | Owner | Status |
| --- | --- | --- | --- | --- | --- |
| `src/butlers/context_bus.py::get_active_context` | read all active signals | all | `situation_read` per kind (`perception.situation`, owner audience) | bu-enbrs2 | retire last, after every reader below moves |
| `src/butlers/context_bus.py::is_user_in_context` | read one signal | all | `situation_read` for the signal's kind | bu-enbrs2 | retire last |
| `src/butlers/context_bus.py::set_context` | write a signal | all but `dnd` | the kind's registered perception writer (see producers below) | bu-enbrs2 | retire last |
| `src/butlers/context_bus.py::clear_context` | clear a signal | all but `dnd` | the same writers, by writing the reverse transition | bu-enbrs2 | retire last |
| `src/butlers/jobs/context_producers.py::<module>` | write: calendar (`meeting`/`focused`), home presence (`at_home`, `in_space`), travel (`traveling`), commuting ETA (`commuting`), sleep window (`sleeping`) | `meeting`, `focused`, `at_home`, `in_space`, `traveling`, `commuting`, `sleeping` | activity signals: `context.activity` (`perception_context_writer`, `owner.activity`); `at_home`: `home.dots` presence reducer (bu-jv1moa.17), never a fork; `in_space` (room): deferred, no perception kind holds a room | bu-enbrs2 | replace at switch; `in_space` deferred to bu-enbrs2 |
| `roster/general/modules/tools.py::register_tools` | model tools `check_context`, `set_context`, `clear_context` | all but `dnd` | read: `situation.read` capability (PR1, dark); write: none for models; owner-declared states go through owner modes (sw_077 entries, owner-only) | bu-enbrs2 | replace at switch; model write path removed, not replaced |
| `src/butlers/core/attention_ledger.py::get_suppressing_context` | read for routine notify suppression | `dnd`, `sleeping` | `owner.suppression`, `owner.activity` and owner mode holds | bu-dhloi1.22 | deferred to bu-dhloi1.22 (derived urgency and owner delivery) |
| `roster/switchboard/tools/insight/broker.py::_get_suppressing_context_signal_detail` | read for insight suppression | `dnd`, `focused`, `meeting`, `sleeping`, `traveling` | `owner.suppression`, `owner.activity` and owner mode holds | bu-dhloi1.22 | deferred to bu-dhloi1.22 |
| `src/butlers/core/spawner_context.py::fetch_situational_context_preamble` | read for the session preamble | all | bounded starting context from typed reads | bu-dhloi1.8 | deferred to bu-dhloi1.8 |
| `src/butlers/perception/legacy_parity.py::<module>` | read all active signals for shadow parity (dark) | all | none: it exists to retire the bus and goes with it | bu-enbrs2 | retire with the table |
| `src/butlers/core/tool_presentation_inventory.py::<module>` | declares the store's readers and writers | n/a | updated with the tools above | bu-enbrs2 | follows the tool rows |
| `src/butlers/db_authority/grants.py::<module>` | the table's grant map | n/a | dropped with the table | bu-enbrs2 | follows retirement |
| `src/butlers/api/audit_emit.py::emit_dashboard_audit` | namesake: the audit row's request context field | n/a | not this table | none | namesake, stays |
| `src/butlers/api/routers/audit.py::log_audit_entry` | namesake: the audit row's request context field | n/a | not this table | none | namesake, stays |
<!-- /census -->

Also outside the guard: `scripts/init-db.sql` hardens the table for the DND
generation (the `dnd` rows live in `public.user_context` under forced RLS), and
the `user_context` column in sw_001 is a namesake on the Switchboard message
table.

## Hardcoded owner zones

Every site below is switch-time, with no code until `OWNER_CLOCK_LIVE`. At the
switch each reads the owner-local time basis (`perception.clock.owner_time_basis`)
instead of the literal.

<!-- census:owner_zone -->
| Reference | Use | Owner | Status |
| --- | --- | --- | --- |
| `src/butlers/perception/clock.py::<module>` | `LEGACY_FALLBACK_ZONE`, the legacy basis shadow parity compares against | bu-enbrs2 | switch-time, removed when `OWNER_CLOCK_LIVE` flips |
| `roster/chronicler/api/models.py::CreateRoutineRequest` | routine zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `roster/chronicler/api/models.py::RollupsResponse` | rollup zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `roster/chronicler/api/models.py::TrendsResponse` | trends zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `roster/chronicler/api/router.py::_resolve_owner_tz_default` | API owner zone fallback | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/api/routers/calendar_workspace.py::<module>` | workspace zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/api/routers/calendar_workspace.py::get_workspace_meta` | workspace meta zone | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/history_repair.py::run_rebuild` | rebuild day boundary | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/jobs.py::run_rebuild_daily_rollups` | rollup rebuild day boundary | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/models.py::DailyRollup` | rollup zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/models.py::Routine` | routine zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/narration.py::<module>` | narration zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/rollups.py::<module>` | rollup zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/routines.py::<module>` | routine zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/storage.py::create_declared_routine` | stored routine zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/storage.py::upsert_daily_rollup` | stored rollup zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
| `src/butlers/chronicler/storage.py::upsert_mined_routine` | stored routine zone default | bu-enbrs2 | switch-time, no code until `OWNER_CLOCK_LIVE` |
<!-- /census -->

## Feeder replay and retention

Feeders keep their own records; none of them writes `public.user_context`. Each
row points at the existing test that pins replay or retention. A path that does
not exist fails the guard.

<!-- census:feeder -->
| Reference | Feeder | Pins |
| --- | --- | --- |
| `tests/perception/test_presence_reducer_postgres.py::test_duplicate_and_concurrent_delivery_make_one_slot_version` | owner presence (`home.dots`, bu-jv1moa.17) | duplicate and concurrent delivery make one slot version |
| `tests/perception/test_presence_reducer_postgres.py::test_leave_enter_and_quick_return_retraction_keep_history` | owner presence | departure retraction keeps stable identity and history |
| `tests/perception/test_perception_postgres.py::test_replay_digest_and_stale_cas_are_immutable` | every perception writer | replay by digest is immutable; a stale CAS refuses |
| `tests/perception/test_perception_postgres.py::test_retraction_is_atomic_stable_and_visible_outside_query_page` | every perception writer | retraction identity is stable |
| `tests/connectors/test_owntracks_connector.py::test_successful_retention_purge_resets_degraded_connector_health` | raw location fixes | raw fixes expire under the connector's retention purge |
| `roster/finance/tests/test_simplefin_bridge.py::test_overlap_window_replays_by_external_id_without_duplicate` | Finance typed records | an overlapping fetch replays without a duplicate |
| `roster/finance/tests/test_facts.py::test_dedup_via_source_message_id` | Finance typed records | a re-delivered message makes one record |
| `roster/travel/tests/test_bookings.py::test_reingesting_same_segment_is_idempotent` | Travel typed records | re-ingesting a segment is idempotent |
| `roster/travel/tests/test_bookings.py::test_deduplicates_on_confirmation_and_source_message_id` | Travel typed records | one booking per confirmation and message |
| `roster/health/tests/test_wellness_ingest.py::test_duplicate_delivery_returns_existing_fact_id` | Health typed records | a duplicate delivery returns the existing record |
| `roster/health/tests/test_clinical_retention.py::<module>` | Health typed records | clinical retention |
<!-- /census -->

`expect.*` routines (`chronicler.expectations`, `perception_expect_writer`,
bu-xxfkhk): no feeder yet. Nothing on main publishes `expect.window`, so there is
no replay or retention to pin until the publisher lands.

## Switch-time obligations

Not guarded by a scan; each is a named prerequisite for retiring the table.

<!-- census:switch -->
| Reference | Obligation | Owner |
| --- | --- | --- |
| `parity-receipt-store` | a durable store for parity receipts, written by the switch-time scheduler that runs `LegacyParity`; the dark comparator only returns receipts and logs one content-blind line | bu-enbrs2 |
| `deletion-needs-receipts` | `public.user_context` is deleted only on stored agreeing receipts for every kind, never on an empty search | bu-enbrs2 |
| `dnd-rows` | the DND generation keeps its `dnd` rows in `public.user_context`; the table cannot drop until that storage moves | bu-enbrs2 |
| `brief-due-watermark` | the brief scheduler persists each key `perception.clock.brief_due` returns over the watermark | bu-enbrs2 |
| `scheduler-time-basis` | the scheduler and briefing read `owner_time_basis` instead of the legacy settings zone | bu-enbrs2 |
<!-- /census -->
