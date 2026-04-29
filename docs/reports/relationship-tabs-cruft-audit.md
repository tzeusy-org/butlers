# Relationship Tabs Cruft Audit

**Bead:** bu-x7fdu.5 — Guard pass before deletion bead bu-x7fdu.6

**Date:** 2026-04-30

---

## Audit 1 — Legacy Table Readers

**Command:**
```bash
rg -n "FROM (notes|interactions|gifts|loans|activity_feed)\b" src/ roster/ tests/ frontend/
```

**Raw output:**
```
roster/relationship/api/router.py:301:                FROM interactions i
roster/relationship/api/router.py:971:                SELECT max(i.occurred_at) FROM interactions i
roster/relationship/api/router.py:1861:        FROM notes
roster/relationship/api/router.py:1893:        FROM interactions
roster/relationship/api/router.py:1927:        FROM gifts
roster/relationship/api/router.py:1963:        FROM loans
roster/relationship/api/router.py:1999:        FROM activity_feed
roster/relationship/tests/test_contact_info.py:894:        "SELECT * FROM activity_feed WHERE contact_id = $1",
src/butlers/scripts/backfill_facts.py:589:        FROM interactions i
src/butlers/scripts/backfill_facts.py:745:        FROM notes n
src/butlers/scripts/backfill_facts.py:791:        FROM gifts g
src/butlers/scripts/backfill_facts.py:842:    rows = await pool.fetch("SELECT * FROM loans ORDER BY created_at ASC")
src/butlers/scripts/backfill_facts.py:1011:        FROM activity_feed a
tests/e2e/test_relationship_flow.py:137:        SELECT * FROM notes
```

### Classification

| File | Lines | Classification | Notes |
|---|---|---|---|
| `roster/relationship/api/router.py` | 301, 971 | **PRODUCTION** — reachable API | `list_contacts` endpoint reads from `interactions` for `last_interaction_at`. Active endpoint. See below. |
| `roster/relationship/api/router.py` | 1861 | **PRODUCTION** — reachable API | `GET /contacts/{id}/notes` endpoint. Target of bu-x7fdu.6 deletion. |
| `roster/relationship/api/router.py` | 1893 | **PRODUCTION** — reachable API | `GET /contacts/{id}/interactions` endpoint. Target of bu-x7fdu.6 deletion. |
| `roster/relationship/api/router.py` | 1927 | **PRODUCTION** — reachable API | `GET /contacts/{id}/gifts` endpoint. Target of bu-x7fdu.6 deletion. |
| `roster/relationship/api/router.py` | 1963 | **PRODUCTION** — reachable API | `GET /contacts/{id}/loans` endpoint. Target of bu-x7fdu.6 deletion. |
| `roster/relationship/api/router.py` | 1999 | **PRODUCTION** — reachable API | `GET /contacts/{id}/feed` endpoint. Target of bu-x7fdu.6 deletion. |
| `roster/relationship/tests/test_contact_info.py` | 894 | **TEST** | `roster/relationship/tests/` — acceptable per reconciliation amendment; cleanup in bu-x7fdu.6.6 |
| `src/butlers/scripts/backfill_facts.py` | 589, 745, 791, 842, 1011 | **MIGRATION SCRIPT** — one-shot backfill | `backfill_facts.py` is a one-time data migration script that reads the old tables to copy their contents into `facts`. It is purposeful and expected to read these tables; it should be preserved or archived (not deleted) when the source tables are dropped. See Discovered-Follow-Ups. |
| `tests/e2e/test_relationship_flow.py` | 137 | **TEST** (e2e) | E2e test directly queries `notes` table as a post-condition assertion. Acceptable test reference; cleanup in bu-x7fdu.6.6. |

### Audit 1 Verdict

**Non-test, non-backfill production hits: `router.py` lines 301 and 971.**

- Lines 1861, 1893, 1927, 1963, 1999 are the five legacy tab endpoints that Group 6 is scheduled to delete — these are the *targets*, not unexpected callers.
- **Lines 301 and 971** (`FROM interactions i` in `list_contacts` and `get_contact_detail`) are production API callers NOT in the delete target list. These are the list-contacts and get-contact-detail endpoints that query `interactions` to compute `last_interaction_at`. These are live, reachable endpoints that will break if the `interactions` table is dropped. This is a **BLOCKER** — see Discovered-Follow-Ups.

---

## Audit 2 — `_log_activity` Callers

**Command:**
```bash
rg -n "_log_activity|tools/feed|activity_feed" src/ roster/ frontend/src/ tests/
```

**Raw output (abridged to non-trivially repetitive):**
```
roster/finance/tools/transactions.py:15:from butlers.tools.finance._helpers import _log_activity, _row_to_dict
roster/finance/tools/transactions.py:638:    await _log_activity(...)
roster/finance/tools/transactions.py:982:    await _log_activity(...)
roster/finance/tools/transactions.py:1056:    await _log_activity(...)
roster/finance/tools/transactions.py:1258:    await _log_activity(...)
roster/finance/tools/transactions.py:1434:    await _log_activity(...)
roster/finance/tools/_helpers.py:37:async def _log_activity(...)  # Finance-scoped no-op stub
roster/relationship/tests/test_dunbar.py:1344:            CREATE TABLE IF NOT EXISTS activity_feed (
roster/relationship/tests/test_jobs.py:1060:# We also need facts and activity_feed for interaction_log
roster/relationship/tests/test_contact_info.py:86,97,98:    activity_feed table fixture creation
roster/relationship/tests/test_contact_info.py:886,887,894,897:    test_contact_info_add_owner_gate_does_not_log_activity
roster/relationship/tests/test_contact_info.py:972:    test logs activity_feed entry on success
roster/relationship/tests/test_loans_entity_id.py:143:    activity_feed fixture
roster/relationship/tests/test_spo_tools.py:87,89:    activity_feed table fixture creation
roster/relationship/tests/test_tools.py:193,204,205:    activity_feed table fixture creation
roster/relationship/tests/test_tools.py:1381,1394,1411,1422:    test_activity_feed_* tests
roster/relationship/tests/test_tools.py:1655,1670,1688:    address activity_feed tests
roster/relationship/tests/test_tools.py:1907:    test_life_event_activity_feed_integration
roster/relationship/tools/feed.py:12:    "The response shape is backward compatible with the legacy activity_feed table"
roster/relationship/tools/feed.py:67:async def _log_activity(pool, contact_id, event_type, detail) -> None
roster/relationship/tools/feed.py:88,114:    body of _log_activity
roster/relationship/tools/dates.py:11:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/dates.py:50:    await _log_activity(...)
roster/relationship/tools/relationships.py:12:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/relationships.py:243,250,300,307:    await _log_activity(...)
roster/relationship/tools/facts.py:17:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/facts.py:137:    await _log_activity(...)
roster/relationship/tools/contacts.py:13:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/contacts.py:291,409,546,677:    await _log_activity(...)
roster/relationship/tools/contacts.py:615:    ("activity_feed", "contact_id"),
roster/relationship/tools/contact_info.py:15:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/contact_info.py:256,374,422:    await _log_activity(...)
roster/relationship/tools/interactions.py:26:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/interactions.py:169:    await _log_activity(...)
roster/relationship/tools/tasks.py:25:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/tasks.py:121,253,275:    await _log_activity(...)
roster/relationship/tools/groups.py:12:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/groups.py:53:    await _log_activity(...)
roster/relationship/tools/loans.py:32:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/loans.py:181,262:    await _log_activity(...)
roster/relationship/tools/life_events.py:26:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/life_events.py:215:    await _log_activity(...)
roster/relationship/tools/notes.py:27:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/notes.py:128:    await _log_activity(...)
roster/relationship/tools/__init__.py:55,135:    _log_activity exported from __init__
roster/relationship/tools/stay_in_touch.py:11:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/stay_in_touch.py:36,43:    await _log_activity(...)
roster/relationship/tools/gifts.py:31:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/gifts.py:119,200:    await _log_activity(...)
roster/relationship/tools/labels.py:11:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/labels.py:33:    await _log_activity(...)
roster/relationship/tools/addresses.py:10:from butlers.tools.relationship.feed import _log_activity
roster/relationship/tools/addresses.py:64,135,147:    await _log_activity(...)
roster/relationship/migrations/003_consolidate_contacts_to_public.py:53:    FK to activity_feed
roster/relationship/migrations/001_relationship_tables.py:168,177,178,183:    CREATE/DROP activity_feed table
roster/relationship/api/router.py:1999:    FROM activity_feed
src/butlers/scripts/backfill_facts.py:9,1002,1011,1018,1043,1059:    backfill_facts reads activity_feed
src/butlers/modules/contacts/backfill.py:244,900,927,938,958,986,992,996:    ContactBackfill._log_activity (private method; writes to activity_feed)
tests/reconciliation/test_incident_2026_04_21_replay.py:132:    comment mentioning _log_activity fetchrow
tests/tools/test_relationship_types.py:64:    activity_feed fixture
tests/tools/test_contact_entity_lifecycle.py:33,118,131,149,183:    _log_activity mocked
tests/config/test_schema_matrix_migrations.py:60:    "activity_feed" in schema list
tests/features/test_vcard.py:34,102:    public.activity_feed teardown
tests/integration/test_idempotent_ingestion.py:124,133,134,137:    activity_feed fixture
```

### Classification

| Caller | Classification | Notes |
|---|---|---|
| `roster/relationship/tools/feed.py` | **PRODUCTION** — definition | This is `_log_activity`'s home. Group 6.3 must rework or eliminate it. |
| `roster/relationship/tools/notes.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/interactions.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/gifts.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/loans.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/dates.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/contacts.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/contact_info.py` | **PRODUCTION** — listed expected caller | Must be addressed in Group 6.3. |
| `roster/relationship/tools/relationships.py` | **PRODUCTION** — unlisted caller | Not in original expected-callers list; calls `_log_activity` 4 times (lines 243, 250, 300, 307). This is an unexpected production caller but within the same tool layer already targeted by Group 6.3. Not a blocker; Group 6.3 just needs to include it. |
| `roster/relationship/tools/facts.py` | **PRODUCTION** — unlisted caller | Calls `_log_activity` once (line 137). Same situation as `relationships.py`. Group 6.3 must include it. |
| `roster/relationship/tools/tasks.py` | **PRODUCTION** — unlisted caller | Calls `_log_activity` 3 times (121, 253, 275). Group 6.3 must include it. |
| `roster/relationship/tools/groups.py` | **PRODUCTION** — unlisted caller | One call (line 53). Group 6.3 must include it. |
| `roster/relationship/tools/life_events.py` | **PRODUCTION** — unlisted caller | One call (line 215). Group 6.3 must include it. |
| `roster/relationship/tools/stay_in_touch.py` | **PRODUCTION** — unlisted caller | Two calls (36, 43). Group 6.3 must include it. |
| `roster/relationship/tools/labels.py` | **PRODUCTION** — unlisted caller | One call (line 33). Group 6.3 must include it. |
| `roster/relationship/tools/addresses.py` | **PRODUCTION** — unlisted caller | Three calls (64, 135, 147). Group 6.3 must include it. |
| `roster/relationship/tools/__init__.py` | **PRODUCTION** — re-export | Re-exports `_log_activity` (lines 55, 135). Group 6.3 must clean up. |
| `roster/relationship/migrations/001_relationship_tables.py` | **MIGRATION** | Creates/drops `activity_feed` table. Group 6.6 handles migration cleanup. |
| `roster/relationship/migrations/003_consolidate_contacts_to_public.py` | **MIGRATION** | FK reference to `activity_feed`. Group 6.6 handles migration cleanup. |
| `roster/finance/tools/_helpers.py:37` | **PRODUCTION** — separate butler, different `_log_activity` | This is a **different** `_log_activity` defined in the finance butler. It is a no-op stub that does NOT write to `activity_feed`. It is NOT a relationship butler caller and is out of scope for bu-x7fdu.6. |
| `roster/finance/tools/transactions.py` | **PRODUCTION** — finance butler | Calls the finance-scoped no-op `_log_activity` stub. Out of scope for bu-x7fdu.6. |
| `src/butlers/modules/contacts/backfill.py` | **PRODUCTION** — contacts module | `ContactBackfill` has its own private `_log_activity` method (line 986) that directly inserts into `activity_feed`. This is a **live, reachable production caller** outside the relationship tool layer. See Discovered-Follow-Ups. |
| `src/butlers/scripts/backfill_facts.py` | **MIGRATION SCRIPT** | One-shot backfill; expected to reference `activity_feed`. |
| All `roster/relationship/tests/` hits | **TEST** | Acceptable; cleanup in bu-x7fdu.6.6. |
| All `tests/` hits | **TEST** | Acceptable; cleanup in bu-x7fdu.6.6. |

### Complete `_log_activity()` Callsite Inventory for bu-x7fdu.6.3

All production callsites that Group 6.3 must address (import + all await calls):

| File | Import Line | Call Lines |
|---|---|---|
| `roster/relationship/tools/feed.py` | — (definition) | 67 (def), 88, 114 |
| `roster/relationship/tools/notes.py` | 27 | 128 |
| `roster/relationship/tools/interactions.py` | 26 | 169 |
| `roster/relationship/tools/gifts.py` | 31 | 119, 200 |
| `roster/relationship/tools/loans.py` | 32 | 181, 262 |
| `roster/relationship/tools/dates.py` | 11 | 50 |
| `roster/relationship/tools/contacts.py` | 13 | 291, 409, 546, 677 |
| `roster/relationship/tools/contact_info.py` | 15 | 256, 374, 422 |
| `roster/relationship/tools/relationships.py` | 12 | 243, 250, 300, 307 |
| `roster/relationship/tools/facts.py` | 17 | 137 |
| `roster/relationship/tools/tasks.py` | 25 | 121, 253, 275 |
| `roster/relationship/tools/groups.py` | 12 | 53 |
| `roster/relationship/tools/life_events.py` | 26 | 215 |
| `roster/relationship/tools/stay_in_touch.py` | 11 | 36, 43 |
| `roster/relationship/tools/labels.py` | 11 | 33 |
| `roster/relationship/tools/addresses.py` | 10 | 64, 135, 147 |
| `roster/relationship/tools/__init__.py` | 55 | 135 (re-export) |
| `src/butlers/modules/contacts/backfill.py` | — (private method) | 900, 927, 938, 958, 996 |

**Note:** `roster/finance/tools/` has its own same-named but entirely separate `_log_activity` — do NOT touch those in bu-x7fdu.6.

---

## Audit 3 — Frontend Hook Usage

**Command:**
```bash
rg -n "useContactNotes|useContactInteractions|useContactGifts|useContactLoans|useContactFeed" frontend/
```

**Raw output:**
```
frontend/src/hooks/use-contacts.ts:64:export function useContactNotes(contactId: string | undefined) {
frontend/src/hooks/use-contacts.ts:73:export function useContactInteractions(contactId: string | undefined) {
frontend/src/hooks/use-contacts.ts:82:export function useContactGifts(contactId: string | undefined) {
frontend/src/hooks/use-contacts.ts:91:export function useContactLoans(contactId: string | undefined) {
frontend/src/hooks/use-contacts.ts:100:export function useContactFeed(contactId: string | undefined) {
frontend/src/components/relationship/ContactDetailView.tsx:45:  useContactFeed,
frontend/src/components/relationship/ContactDetailView.tsx:46:  useContactGifts,
frontend/src/components/relationship/ContactDetailView.tsx:47:  useContactInteractions,
frontend/src/components/relationship/ContactDetailView.tsx:48:  useContactLoans,
frontend/src/components/relationship/ContactDetailView.tsx:49:  useContactNotes,
frontend/src/components/relationship/ContactDetailView.tsx:840:  const { data: notes, isLoading } = useContactNotes(contactId);
frontend/src/components/relationship/ContactDetailView.tsx:866:  const { data: interactions, isLoading } = useContactInteractions(contactId);
frontend/src/components/relationship/ContactDetailView.tsx:904:  const { data: gifts, isLoading } = useContactGifts(contactId);
frontend/src/components/relationship/ContactDetailView.tsx:946:  const { data: loans, isLoading } = useContactLoans(contactId);
frontend/src/components/relationship/ContactDetailView.tsx:1004:  const { data: feed, isLoading } = useContactFeed(contactId);
```

### Classification

| File | Classification | Notes |
|---|---|---|
| `frontend/src/hooks/use-contacts.ts` | **EXPECTED** — hook definitions | This is the file Group 6.4 edits. All five hooks are defined here. |
| `frontend/src/components/relationship/ContactDetailView.tsx` | **EXPECTED** — already rewritten in Group 4 | Imports and uses all five hooks. This is the component Group 4 already targeted. Per the issue brief, this is an expected consumer. |

### Audit 3 Verdict

Exactly the two files called out in the issue — `use-contacts.ts` (Group 6.4 target) and `ContactDetailView.tsx` (Group 4 target). No unexpected callers. **Audit 3 is clean.**

---

## Group 6 Block Status

### BLOCKER: `router.py` lines 301 and 971 — `last_interaction_at` via `interactions` table

`GET /contacts` (list endpoint, line 301) and `GET /contacts/{id}` (detail endpoint, line 971) both subquery `FROM interactions i` to compute `last_interaction_at`. These are live, reachable production API endpoints that are **not** in the bu-x7fdu.6 delete target list.

If the `interactions` table is dropped in Group 6 without addressing these two callers, both endpoints will fail at runtime. **Group 6 is BLOCKED until this is resolved.**

Disposition options (for coordinator/Group 6 author to decide):
1. Replace the subquery with a fact-layer equivalent (e.g. a stored `last_interaction_at` fact on the entity).
2. Add lines 301 and 971 explicitly to the Group 6 deletion/migration scope.
3. File a dedicated bug bead and gate Group 6 on its resolution.

### BLOCKER: `src/butlers/modules/contacts/backfill.py` — private `_log_activity` writing to `activity_feed`

`ContactBackfill._log_activity` (line 986) directly inserts into `activity_feed` and is called from live sync code paths (lines 900, 927, 938, 958). This is production code in `src/butlers/modules/` — not in the relationship tool layer and not covered by Group 6.3's tool-layer sweep.

If `activity_feed` is dropped without updating `ContactBackfill`, the contacts module sync will begin raising exceptions on every activity log call. **Group 6 is BLOCKED until this is resolved.**

### NOT A BLOCKER: Unlisted `_log_activity` callers in relationship tools

Eight tool files (`relationships.py`, `facts.py`, `tasks.py`, `groups.py`, `life_events.py`, `stay_in_touch.py`, `labels.py`, `addresses.py`) call `_log_activity` but were not in the original Group 6.3 expected-callers list. These are all within the relationship tool layer that Group 6.3 already targets. They are not blockers — Group 6.3 simply needs to expand its callsite inventory to include them (the complete list is in the Audit 2 callsite table above).

### NOT A BLOCKER: `backfill_facts.py` reading legacy tables

`src/butlers/scripts/backfill_facts.py` reads all five legacy tables. This is a one-shot migration script whose entire purpose is to copy those tables into `facts` before deletion. Once Group 6 drops the tables, this script will be a no-op (or error) on subsequent runs, which is expected. It should be archived or have its relationship phase disabled after the tables are dropped.

---

## Discovered-Follow-Ups

The following items are out of scope for this bead (bu-x7fdu.5) and should be filed as new beads by the coordinator before bu-x7fdu.6 proceeds:

1. **`router.py` `last_interaction_at` subquery** — `GET /contacts` (line 301) and `GET /contacts/{id}` (line 971) subquery `FROM interactions`. These must be migrated to a fact-layer or entity-level field before the `interactions` table is dropped. Coordinator should file this as a blocker bead against bu-x7fdu.6.

2. **`ContactBackfill._log_activity` in `src/butlers/modules/contacts/backfill.py`** — Private method that directly inserts into `activity_feed` (lines 900, 927, 938, 958, 996). Must be rerouted (e.g., to `store_fact` or dropped) before `activity_feed` is dropped. Coordinator should file this as a blocker bead against bu-x7fdu.6.

3. **`backfill_facts.py` archival** — After Group 6 drops the legacy tables, the relationship phase of `src/butlers/scripts/backfill_facts.py` should be archived or marked as complete-and-disabled to prevent confusion. Low priority; can be post-Group-6 cleanup.
