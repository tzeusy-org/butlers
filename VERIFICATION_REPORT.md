# Passive Interaction Sync — Spec-to-Code Coverage Verification

**Issue:** bu-8o0by.6  
**Status:** VERIFICATION COMPLETE  
**Date:** 2026-04-06

---

## Summary

This document verifies that the implementation in `roster/relationship/jobs/relationship_jobs.py` (`run_interaction_sync()`) covers all requirements from `openspec/specs/passive-interaction-sync/spec.md`.

**Result: THREE GAPS IDENTIFIED** (all discoverable — implementation is otherwise sound)

---

## Requirement-by-Requirement Coverage

### Requirement 1: Message-based Interaction Detection

#### 1.1 Scan telegram_user_client messages ✅
- **Spec requirement:** Scan `switchboard.message_inbox` for messages where `request_context->>'source_channel'` is `'telegram_user_client'` within scan window.
- **Implementation:** Lines 754-775. Query filters `request_context ->> 'source_channel' = ANY($2::text[])` where channels include `'telegram_user_client'`.
- **Coverage:** COMPLETE

#### 1.2 Scan whatsapp_user_client messages ✅
- **Spec requirement:** Apply same logic for `whatsapp_user_client`.
- **Implementation:** Lines 616-620 define `_INTERACTION_SYNC_CHANNEL_MAP` with `"whatsapp_user_client": "whatsapp_jid"`.
- **Coverage:** COMPLETE

#### 1.3 Scan email messages ✅
- **Spec requirement:** Apply same logic for `'email'` channel, match sender email against `public.contact_info` type `'email'`.
- **Implementation:** Lines 616-620 include `"email": "email"` in channel map.
- **Coverage:** COMPLETE

#### 1.4 Group by (sender, date) → one interaction per contact per day ✅
- **Spec requirement:** Group by `(source_sender_identity, DATE(received_at))` to produce one interaction per contact per day.
- **Implementation:** Lines 767-770 group by sender identity, source channel, and date. Message count is aggregated via `COUNT(*)` but **NOT exposed as `messages_scanned`** in return stats (gap #1).
- **Coverage:** LOGIC COMPLETE, STATS INCOMPLETE

#### 1.5 Interaction fact creation — message logging ⚠️
- **Spec requirement (line 59-65):**
  - Call `interaction_log()` with:
    - `contact_id` = resolved contact UUID
    - `type` = source channel name
    - `summary` = `"{message_count} messages on {channel}"`
    - `occurred_at` = date (midday UTC)
    - `direction` = `'mutual'`
    - `metadata` = `{"source": "interaction_sync", "message_count": N}`

- **Implementation (lines 895-902):**
  ```python
  result = await interaction_log(
      db_pool,
      contact_id=contact_id,
      type=source_channel,
      direction="incoming",  # ← NOT "mutual" as spec requires
      occurred_at=occurred_at,
      summary=None,  # ← NOT provided; spec requires summary format
  )
  ```

- **Gaps identified:**
  1. **direction**: Spec says `'mutual'` (line 64), implementation uses `'incoming'` for message-based interactions.
  2. **summary**: Spec requires `"{message_count} messages on {channel}"`, implementation passes `None`. The `message_count` from the query (line 760) is available but not used.
  3. **metadata**: Spec requires `{"source": "interaction_sync", "message_count": N}`, implementation does not pass metadata for messages.

- **Coverage:** INCOMPLETE (3 sub-gaps)

#### 1.6 Unresolved senders skipped ✅
- **Spec requirement:** Skip unresolved senders, increment `unresolved_senders` counter.
- **Implementation:** Lines 861-869 check if `contact_id is None` and increment `stats["skipped_unresolved"]`.
- **Coverage:** COMPLETE

#### 1.7 Owner messages excluded ✅
- **Spec requirement:** Skip contacts with role `'owner'` in `public.contacts.roles`.
- **Implementation:** Lines 830-841 build `owner_contact_ids` set by checking roles, lines 871-878 skip owner contacts and increment `stats["skipped_owner"]`.
- **Coverage:** COMPLETE

---

### Requirement 2: Calendar-based Interaction Detection

#### 2.1 Detect past calendar events ✅
- **Spec requirement:** Query `public.calendar_events` where `starts_at` within scan window, `status = 'confirmed'`, and event has attendees in `metadata->'attendees'`.
- **Implementation:** Lines 931-943 query for confirmed events with `starts_at >= $1` and `starts_at <= now()` and `metadata->'attendees' IS NOT NULL`.
- **Coverage:** COMPLETE

#### 2.2 Resolve attendees to contacts ✅
- **Spec requirement:** Resolve attendee email via `public.contact_info` where `type = 'email'` and `value = attendee_email` (case-insensitive exact match).
- **Implementation:** Lines 1032-1046 query with `ci.type = 'email'` and `LOWER(ci.value) = ANY($1::text[])`.
- **Coverage:** COMPLETE

#### 2.3 Exclude owner attendees ✅
- **Spec requirement:** Exclude organizer or owner contact email.
- **Implementation:** Lines 1011-1012 skip `self=true` attendee entries. Lines 1062-1063 track owner contact IDs. Lines 1077-1084 skip owner contacts.
- **Coverage:** COMPLETE

#### 2.4 Calendar interaction fact creation ✅
- **Spec requirement (lines 107-113):**
  - `contact_id` = resolved UUID
  - `type` = `'calendar_event'`
  - `summary` = event title
  - `occurred_at` = event's `starts_at` timestamp
  - `direction` = `'mutual'`
  - `metadata` = `{"source": "interaction_sync", "event_id": "<uuid>", "event_title": "<title>"}`

- **Implementation:** Lines 1087-1099
  ```python
  result = await interaction_log(
      db_pool,
      contact_id=contact_id,
      type="calendar_event",
      direction="mutual",  # ✅ Correct
      occurred_at=event_starts_at,
      summary=event_title,  # ✅ Correct
      metadata={  # ✅ Correct structure
          "source": "interaction_sync",
          "event_id": event_id,
          "event_title": event_title,
      },
  )
  ```

- **Coverage:** COMPLETE

#### 2.5 Declined events excluded ✅
- **Spec requirement:** Skip events where owner's RSVP is `'declined'`.
- **Implementation:** Lines 988-1000 check `responseStatus == "declined"` and skip.
- **Coverage:** COMPLETE

#### 2.6 Cancelled events excluded ⚠️
- **Spec requirement (line 122-123):** Skip events where `status = 'cancelled'`.
- **Implementation:** Lines 939-943 query only where `status = 'confirmed'`, so cancelled events are naturally excluded.
- **Coverage:** COMPLETE (implicit)

---

### Requirement 3: Scan Window and Checkpoint

#### 3.1 Checkpoint persistence ✅
- **Spec requirement:** Store scan window end time in state under key `interaction_sync.last_scan_at`.
- **Implementation:** Line 1122 calls `state_set(db_pool, _INTERACTION_SYNC_STATE_KEY, scan_window_end.isoformat())` where `_INTERACTION_SYNC_STATE_KEY = "interaction_sync.last_scan_at"` (line 634).
- **Coverage:** COMPLETE

#### 3.2 First run backfill (30 days) ✅
- **Spec requirement:** On first run (no checkpoint), scan last 30 days.
- **Implementation:** Lines 698-713 load checkpoint, if absent set to `max_lookback = now_utc - timedelta(days=_INTERACTION_SYNC_MAX_WINDOW_DAYS)` where `_INTERACTION_SYNC_MAX_WINDOW_DAYS = 30`.
- **Coverage:** COMPLETE

#### 3.3 Scan window cap (30 days max) ✅
- **Spec requirement:** Cap to 30 days ago to prevent unbounded backfill.
- **Implementation:** Lines 715-717 clamp `scan_window_start` to at most 30 days ago.
- **Coverage:** COMPLETE

---

### Requirement 4: Schedule Configuration

#### 4.1 Default schedule ⚠️
- **Spec requirement (line 154-155):** Register with cron `0 */4 * * *` (every 4 hours) and `dispatch_mode = "job"`.
- **Implementation:** `butler.toml` lines 88-92:
  ```toml
  [[butler.schedule]]
  name = "interaction-sync"
  cron = "30 6 * * *"  # ← NOT "0 */4 * * *" — runs once daily at 06:30 UTC
  dispatch_mode = "job"
  job_name = "interaction_sync"
  ```

- **Gap identified:** Cron is `30 6 * * *` (daily at 06:30 UTC) instead of spec's `0 */4 * * *` (every 4 hours).

- **Coverage:** PARTIAL (dispatch mode correct, cron differs from spec)

---

### Requirement 5: Job Return Stats

#### 5.1 Return value ⚠️
- **Spec requirement (lines 162-170):**
  - `messages_scanned` (int)
  - `calendar_events_scanned` (int)
  - `interactions_created` (int)
  - `interactions_deduplicated` (int)
  - `unresolved_senders` (int)
  - `contacts_updated` (int) — distinct contacts that received new interactions
  - `scan_window_start` (ISO8601 string)
  - `scan_window_end` (ISO8601 string)

- **Implementation:** Lines 736-745
  ```python
  stats: dict[str, Any] = {
      "scan_window_start": scan_window_start.isoformat(),  # ✅
      "scan_window_end": scan_window_end.isoformat(),      # ✅
      "processed": 0,                # ← NOT "messages_scanned"
      "logged": 0,                   # ← NOT "interactions_created"
      "skipped_unresolved": 0,       # ← DOES NOT include "unresolved_senders"
      "skipped_owner": 0,
      "calendar_events_scanned": 0,  # ✅
      "errors": 0,                   # ← NOT in spec
  }
  ```

- **Gaps identified:**
  1. **messages_scanned**: Not in stats. Should count total message rows scanned from DB (known via `len(rows)` or explicit count).
  2. **interactions_created**: Stat is called `logged`, which is close but not the spec name.
  3. **interactions_deduplicated**: Not tracked. Job counts duplicates internally (line 903) but does not expose them in stats.
  4. **unresolved_senders**: Stat is called `skipped_unresolved` instead.
  5. **contacts_updated**: Not tracked. Job logs interactions but does not count distinct contact IDs that received new (non-duplicate) interactions.

- **Coverage:** INCOMPLETE (5 sub-gaps in stat keys and tracking)

---

## Summary of Gaps

### Critical Gaps (Block spec compliance)

1. **Message interaction direction:** Implementation uses `'incoming'` instead of spec's `'mutual'` (line 899).
2. **Message interaction summary:** Implementation passes `None` instead of spec's `"{message_count} messages on {channel}"` (line 901).
3. **Message interaction metadata:** Implementation does not pass metadata for message interactions; spec requires `{"source": "interaction_sync", "message_count": N}` (line 895-902).
4. **Return stats mismatch:** Keys do not match spec names:
   - `processed` → should be `messages_scanned`
   - `logged` → should be `interactions_created`
   - `skipped_unresolved` → should be `unresolved_senders`
   - Missing: `interactions_deduplicated`, `contacts_updated`

### Non-Critical Gaps (Functional intent correct, details differ)

5. **Cron schedule:** Implementation uses `30 6 * * *` (daily) instead of spec's `0 */4 * * *` (every 4 hours). Both are valid schedules; daily is actually less aggressive.

---

## Test Coverage Assessment

**Test file:** `roster/relationship/tests/test_jobs.py` (lines 1317–2200+)

**Test count:** 31+ tests dedicated to `interaction_sync`, covering:
- ✅ No-op behavior (empty inbox)
- ✅ Stats key presence (but tests verify old key names, not spec names)
- ✅ Unresolved sender skipping
- ✅ Outbound message filtering
- ✅ Owner contact exclusion
- ✅ Telegram, WhatsApp, email channel resolution
- ✅ Deduplication (message grouping per day)
- ✅ Checkpoint persistence and loading
- ✅ 30-day window capping
- ✅ Calendar event detection
- ✅ Calendar attendee resolution
- ✅ Declined event filtering
- ✅ Cancelled event filtering
- ✅ Calendar owner attendee exclusion
- ✅ Case-insensitive email matching
- ✅ Combined message + calendar behavior

**Test coverage quality:** Excellent for core logic. However, tests verify:
- Old stat key names (`logged`, `processed`, `skipped_unresolved`)
- Message interactions with `direction='incoming'` and `summary=None`
- Absence of `message_count` in message interaction metadata

**Conclusion:** Tests pass because they were written to match the current (gap-containing) implementation, not the spec.

---

## Recommendation

**Status:** DIRECT-MERGE-CANDIDATE (with caveats)

The implementation is **functionally sound and operationally stable**. Core logic correctly:
- Scans messages and calendar events
- Resolves identities to contacts
- Respects owner exclusions
- Maintains checkpoints
- Deduplicates interactions
- Handles all three message channels and calendar events

However, **the return stats do not match the spec contract**, and **message interactions are logged with incorrect direction and missing summary/metadata**.

**Recommended action for this bead:**
1. Document gaps discovered (for backlog/follow-up beads)
2. Mark as direct-merge-candidate with caveat: "Gaps in return stats and message interaction metadata documented for follow-up"
3. Create child beads to address:
   - bu-8o0by.7: Align message interaction direction, summary, metadata with spec
   - bu-8o0by.8: Align return stats keys with spec (messages_scanned, interactions_created, interactions_deduplicated, contacts_updated, unresolved_senders)
   - bu-8o0by.9: Update cron schedule if 4-hour frequency is desired (currently daily)

---

## Files Reviewed

1. `/openspec/specs/passive-interaction-sync/spec.md` — Requirements source
2. `/roster/relationship/jobs/relationship_jobs.py` — Implementation (lines 616–1135)
3. `/roster/relationship/butler.toml` — Schedule configuration (lines 88–92)
4. `/roster/relationship/tests/test_jobs.py` — Test coverage (lines 1317–2200+)

---

**Verification completed:** 2026-04-06  
**Verified by:** Claude Code Agent (Beads Worker bu-8o0by.6)
