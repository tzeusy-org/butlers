# Chronicler

> **Purpose:** Butler-specific Chronicler contracts that have no other home.
> **Audience:** Developers changing Chronicler adapters, privacy rendering, or editorial endpoints.
> **Prerequisites:** [Butlers](index.md), [Chronicler manifesto](../../roster/chronicler/MANIFESTO.md).

Moved verbatim from `AGENTS.md` (Butler-specific rules without a docs page). Identity and scope live in the manifesto.

- Chronicler adapters whose evidence is mirrored across schemas derive `source_ref` from the
  upstream identifier (`calendar:{origin_instance_ref}`), so the `(source_name, source_ref)` upsert
  dedupes the fan-out. `health.steps` and `health.heart_rate` are point-event sources with no lane
  mapping. `SpotifySessionAdapter` falls back to `track_names` when both context fields are NULL.
- Chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept any
  `?date=&tz=` deterministically via `editorial.compose_briefing_payload`; date navigation is a
  frontend concern.

## Implementation Notes

The adapter, frontend and gap-interview notes below moved from Chronicler's session guidance.
They retain their original decision context and do not expand the runtime's approved read or
write surfaces. The [Chronicler spec](../../openspec/specs/butler-chronicler/spec.md),
[manifesto](../../roster/chronicler/MANIFESTO.md) and migration-tracked grants remain authoritative.
The scope question between the shipped gap interview and Retrospective-Only Scope remains
held for the owner in `bu-idc5mx.8`; this relocation does not resolve it.

## Adapter defaults

- **`spotify.session_summary`**: `privacy=normal`. Track names and duration are
  not sensitive. Per-row overrides remain available via the correction mechanism.
- **`owntracks.points` (point events)**: `privacy=normal`. The Chronicles
  dashboard is the owner's view of their own location history; blanket masking
  hid the trail and made the Map widget useless. Per-recipient masking for
  shared/screenshot views should be reintroduced via an explicit toggle, not
  by default classification. Backfilled to `normal` via core_086.
- **`owntracks.points` (movement episodes)**: `privacy=normal`. Same rationale
  as point events. Backfilled to `normal` via core_086.

## Frontend contract

- Gantt swimlane: every non-restricted episode renders a bar in its lane.
  Sensitive bars use a hatched fill and show `"<Category>: <duration>"` in
  the tooltip (e.g. "Travel: 38 min"). The `canonical_title` is never exposed
  for sensitive episodes.
- EpisodeDrawer: sensitive episodes show the envelope (Start, End, Duration)
  but mask title, source, and all payload-level fields.
- Restricted episodes are filtered server-side and never returned by the API.

## CoreSessionsAdapter: episode title-resolution rules (bu-fkqv0)

`CoreSessionsAdapter._compute_episode_title` derives a human-readable title
for each projected `work` episode.  The rules are applied in priority order:

| Condition | Episode title |
|-----------|--------------|
| `trigger_source='route'` AND contact resolved | `Conversation with {display_name}` |
| `trigger_source='route'` AND contact unresolved | `Conversation via {channel}` (e.g. `via telegram`) |
| `trigger_source='route'` AND no channel | `Conversation via unknown channel` |
| `trigger_source` in `trigger`, `external`, `dashboard` | `{schema}: manual task` |
| `trigger_source` NULL or unrecognised | `{schema} session` *(legacy fallback)* |

Contact resolution is performed by `_resolve_contacts`, which JOINs:

```
{schema}.sessions.ingestion_event_id
  → public.ingestion_events.id
  → relationship.entity_facts(predicate=<channel predicate>, object=source_sender_identity)
  → public.entities.canonical_name
```

The `source_channel` maps to a predicate (`email` → `has-email`, `phone` →
`has-phone`, the `telegram*`/`whatsapp_jid` channels → `has-handle`), matched
against active literal triples.

The JOIN is guarded: if `public.ingestion_events`, `relationship.entity_facts`,
or `public.entities` are absent (e.g. before migration), the adapter degrades to
`(None, None)` and
falls through to `'Conversation via unknown channel'` for route sessions.

Only `trigger_source='route'` rows with a non-NULL `ingestion_event_id` are
resolved; all other rows skip the JOIN entirely.

The title is written to `chronicler.episodes.title`.  Because `source_ref` is
`{schema}.sessions:{session_id}`, re-running the adapter with a reset watermark
re-projects all titles idempotently in-place (no backfill migration needed for
forward-projected rows; existing `{schema} session` rows from before this change
require a watermark reset to be re-titled).

**Backfill note (bu-fkqv0 follow-up):** Existing episodes titled `{schema} session`
where the underlying session has a resolvable contact can be re-titled by resetting
the per-schema watermark in `projection_checkpoints` to `NULL` and running the
adapter.  This was not automated in the initial PR; track as a follow-up bead if
needed.

## CoreSessionsAdapter: excluded trigger_source values

`CoreSessionsAdapter` (`src/butlers/chronicler/adapters/sessions.py`) filters
out session rows that are operational telemetry rather than user activity.
The exclusion is applied at the SQL layer (not post-fetch) so the per-schema
watermark advances only over user-visible rows.

Excluded (exact match): `tick`, `classification`, `qa`, `healing`
Excluded (prefix match): `schedule:*`

Rationale: heartbeat ticks, Switchboard classification sessions, QA probe sessions, healing sessions, and all
scheduler-fired background jobs dominate raw session counts but carry no
"lived past time" signal.  They should never appear in the Chronicles "Work"
lane.

### `deadline:*` trigger_source: decision and rationale (bu-ve8ne)

**Decision: `deadline:*` sessions are INCLUDED (not excluded) in the Tasks lane.**

`deadline:<task-name>` sessions are fired by the scheduler when a deadline
threshold date is crossed (e.g., "passport expires in 30 days"). Although the
dispatch is butler-initiated (same mechanical origin as `schedule:*`), the
distinction is semantic:

- `schedule:*` sessions are **pure butler-internal housekeeping** (cron health
  checks, day-close bundles, connector polling, etc.). They carry no user-intent
  signal.
- `deadline:*` sessions are **user-proxied work**: the user established the
  deadline via `deadline_create`; the butler agent session executes meaningful
  notification logic on their behalf. These events represent real-world deadlines
  the user cares about and are meaningful "lived past time" entries.

The original bu-x096m design note called this "mixed user-set vs butler-set,
needs a marker." After investigation, the conclusion is that **no marker is
needed**: every `deadline:*` dispatch corresponds to a user-intent deadline task
running to completion. The full `deadline:*` namespace belongs in Tasks.

The frontend `lane-taxonomy.ts` already correctly maps `deadline:*` to the
"tasks" lane (all non-`route` trigger_source values), so no frontend change
is needed either.

If a future need arises to distinguish butler-internal deadline housekeeping
from user-visible deadline alerts, introduce a `deadline:internal:*` prefix
for the housekeeping variant and exclude that prefix. Do not exclude the
bare `deadline:*` namespace.

To add a new excluded source, update `EXCLUDED_TRIGGER_SOURCES` (exact) or
`EXCLUDED_TRIGGER_SOURCE_PREFIX` (prefix) in `adapters/sessions.py` and add
a corresponding test case in `tests/chronicler/test_core_sessions_adapter.py`.

**Ops verification post-deploy:** after one cron tick, run
```sql
SELECT payload->>'trigger_source' AS trigger_source, count(*)
FROM chronicler.episodes
WHERE source_name = 'core.sessions'
  AND start_at > now() - interval '10 minutes'
GROUP BY 1;
```
`tick`, `qa`, `healing`, and any `schedule:*` values must not appear.
`deadline:*` values MAY appear and are expected in the Tasks lane.

## Frontend timezone source (bu-k18cm)

All timestamps in the Chronicles frontend (Gantt, Scrubber, EpisodeDrawer,
axis tick labels) render in the **owner's configured timezone**, not the
browser's local timezone.

**Source:** `GET /api/settings/general` → `data.timezone` (IANA name, e.g. `"Asia/Singapore"`).

**Fallback:** `"Asia/Singapore"` (matches the `SGT` constant in `briefing.py`),
used while the API call is in-flight or returns an error.

**Implementation:**
- `App.tsx` fetches the timezone via `useGeneralSettings()` and mounts one
  `<AppTimezoneProvider timezone={ownerTz}>` around all routes.
- `ChroniclesPage` reads `ownerTz` via `useTimezone()`, derives the latest
  settled owner-local day with `dayKeyInTimeZone()` / `shiftDayKey()`, includes
  `tz: ownerTz` in briefing and day-close refresh requests, and passes
  `{date, tz}` to `ChroniclesDrilldownPanel`; it does not fetch the setting or
  mount another provider.
- `ChroniclesDrilldownPanel` derives the selected day's `[from, to)` UTC window
  with `dayWindowInTz(date, tz)` from `frontend/src/lib/tz-format.ts`.
- Child components read the timezone directly via `useTimezone()` from
  `frontend/src/components/ui/timezone-context.tsx`.
- All formatting uses `date-fns-tz` (`formatInTimeZone`, `fromZonedTime`); never
  `Date.toLocaleString` or `Date.toLocaleTimeString`.

## CommsSocialAdapter: comms -> Social projection (bu-jc6htw.1)

`src/butlers/chronicler/adapters/comms.py` (`source_name = "comms.message_bursts"`,
`episode_type = "social_episode"`) projects already-ingested inbound message
metadata from `public.ingestion_events` into `social` activity candidates.
It is the Tier-1 half of tasks.md §6.2 (IEA reframe, "Comms Projected Into
Social").

**Read surface:** `public.ingestion_events` (metadata only: id, received_at,
source_channel, source_sender_identity; never message content, never
`switchboard.message_inbox`) + `relationship.entity_facts` for participant
resolution. The `relationship.entity_facts` grant to `butler_chronicler_rw`
required its own migration (`core_150_chronicler_comms_entity_facts_grant`).
It was previously used cross-schema only by `CoreSessionsAdapter._resolve_contacts`
(bu-hjo3i) without an explicit RFC 0014 §D8 grant migration ever having been
filed for it; that gap is now closed for both callers.

**`source_channel` → connector mapping** (the *connector module name* the bead
brief used is NOT the persisted `source_channel` value; verify against the
connector's `_build_ingest_envelope` call site, not its filename):

| Connector module | `ingestion_events.source_channel` |
|---|---|
| `gmail.py` | `"email"` |
| `telegram_bot.py` | `"telegram_bot"` |
| `telegram_user_client.py` | `"telegram_user_client"` |
| `whatsapp_user_client.py` | `"whatsapp_user_client"` |
| `discord_user.py` | `"discord"` (not `"discord_user"`) |

**Participant match-object encoding** (mirrors how `relationship` writes
`entity_facts`, see migrations 019/027/028 and
`roster/relationship/tools/relationship_assert_fact.py`): email is reduced to
a bare lowercased address (Gmail's `source_sender_identity` is the raw RFC-822
`From:` header, not a bare address); both Telegram channels are matched
`telegram:`-prefixed; Discord and WhatsApp are matched verbatim; those two
channels are **not currently auto-linked by ingress**
(`identity._CHANNEL_TYPE_TO_PREDICATE` has no entries for `"discord"` or
`"whatsapp_user_client"`, so `assert_sender_channel_fact()` silently no-ops for
them today), so verbatim resolution only succeeds if the owner separately
registered a `has-handle` fact for that raw id. This is a known, accepted gap,
not a bug in this adapter.

**Burst semantics:** contiguous `ingestion_events` sharing
`(source_channel, source_sender_identity)` within `BURST_GAP_MINUTES` (20)
collapse into one `social_episode`. Deliberately **no cross-batch carryover**
(unlike `OwnTracksPointAdapter`). A burst straddling a batch boundary may
fragment into two candidates; tasks.md §7's day-close deterministic
reconciliation is the designed place duplicate/overlapping same-lane
candidates get merged, so Tier-1 projection does not need perfect stitching.

**Confidence ladder:** resolved participant → two independent evidence kinds
(`message_channel` + `entity_resolution`) → `high`. Unresolved → single kind
→ `low` (still counted, degrades gracefully; spec: "Unresolved participant
degrades to UNATTRIBUTED + lower confidence"). `episode_entities` gets a
`role='participant'` row only when resolved; the owner row is always written
via the usual `upsert_owner_episode_entity` convention.

**Atomic owner link:** `upsert_owner_episode_entity` is a plain execute. Every owner-only
adapter calls it inside `async with owner_episode_transaction(conn):` together with its
`upsert_episode`, so a failed link write rolls the episode back (a savepoint when the adapter
is already in a transaction, as in comms, sessions and owntracks_ssid).
`tests/chronicler/test_owner_episode_atomicity_guard.py` fails on a call outside that block.

**Scheduling:** `chronicler_project_comms` job, `*/15 * * * *` (matches
`chronicler_project_sessions`'s cadence: messaging activity is higher-frequency
than the `*/30 * * * *` sources).

## Day-close gap interview (bu-whhll.12)

A **separate, explicitly-opted-in surface**, NOT part of day-close. The
`chronicler_day_close` prompt is forbidden from sending any extra
proactive/correction message and explicitly defers those to "separate,
explicitly-opted-in surfaces"; this is one of them. The day-close narration is
left untouched.

- **Surface:** its own `[[butler.schedule]]` in `butler.toml`
  (`chronicler_gap_interview`, `dispatch_mode="job"`, `cron = "0 9 * * *"` in
  the owner's timezone; no session, bu-idc5mx.4). Runs after the 01:05 day-close, at a civil hour.
  **Max one message per day** (dedupe key `gap_interview:asked:<YYYY-MM-DD>` in
  the KV `state` store).
- **Trigger** (`chronicler/gap_interview.py::evaluate_gap_interview`, pure): the
  closed day left **>2h of waking-window** (06:00–22:00 local, matching
  `editorial.WAKING_HOUR_*`) unaccounted (reusing
  `aggregations.untracked_seconds_for_window` so the prompt can never disagree
  with the dashboard's untracked slice) **OR** a low-confidence
  `occupation_block` is present (occupation blocks are always `confidence=low`).
- **Ask:** `chronicler_gap_interview(date_label, timezone)` does the whole ask
  itself: evaluate → per-day dedupe → quiet-hours/`delivery_preferences` gate →
  deliver a **Telegram inline-button** message (✅ Work day / ✏️ Not work /
  🚫 Dismiss). The tool and the 09:00 job share
  `chronicler/gap_interview_job.py::ask_gap_interview`; the job computes
  yesterday in the general timezone and sends nothing else. The
  telegram send is a self-contained `gap_interview.GapInterviewTransport` impl
  (`gap_interview_telegram.TelegramInlineButtonTransport`) that resolves the
  owner chat (`public.entity_info`) + bot token (`butler_secrets`) and POSTs
  `sendMessage` with a `reply_markup` inline keyboard whose buttons encode
  `cgi:<interview_id>:<answer>` (`interview_id == date_label`, ≤64-byte budget).
- **Answer (round-trip, wired):** the owner's tap fires a `callback_query`. The
  shared `telegram_bot` connector recognises the **prefix-guarded `cgi:`**
  payload (`_maybe_handle_gap_interview_callback`; every other callback keeps
  its existing drop behaviour, additive) and, because the connector runs as the
  restricted `connector_writer` role and cannot write the chronicler schema,
  POSTs `{interview_id, answer}` to `POST /api/chronicler/gap-interview/resolve`
  (env `CONNECTOR_INTERNAL_API_URL`, default `http://dashboard-api:41200`) and
  `answerCallbackQuery`-acks the tap. That endpoint + the
  `chronicler_resolve_gap_interview` MCP tool both delegate to
  `gap_interview.resolve_gap_interview_callback` (one resolver, identical write
  shape). It is the **first real tenant of the corrections machinery**: writes a
  `chronicler.overrides` row (confirm annotates, correct tombstones the inferred
  block, dismiss notes), reinforces/decays the routine's `confidence` via
  `storage.adjust_routine_confidence` (clamped `[0,1]`), and is idempotent
  (telegram re-delivers callbacks). Unknown/expired taps get a graceful toast.
- **Transport isolation (coordinator decision):** ask + answer both sit behind
  `GapInterviewTransport` / `resolve_gap_interview_callback` so they migrate onto
  the **decision loop** (RFC 0021 one-tap approvals, epic bu-24lu6, gated behind
  bu-24lu6.1) with no engine change; only the encoding/route swaps. No new
  migration (overrides/routines exist).
