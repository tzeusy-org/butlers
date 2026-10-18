## Context

Every claim below is read from main `7cfd3b301`. `[Observed]` means read in code or spec at the cited
place. `[Unknown]` means it could not be established from the repository and has a verify task in
tasks.md. Nothing here ran a provider call.

## Today's clocks

- `[Observed]` `IngestEventV1` has a required, timezone-aware `observed_at` and no source-time field
  (`roster/switchboard/tools/routing/contracts.py:171`). The base spec defines `observed_at` as "when
  the connector observed the event".
- `[Observed]` The Switchboard stamps `received_at = datetime.now(UTC)` (`ingest.py:849`) and writes it
  to `switchboard.message_inbox` and `public.ingestion_events`. The `public.ingestion_events` insert
  has no `observed_at` column; `observed_at` is stored inside `message_inbox.raw_payload` only
  (`ingest.py:879`).
- `[Observed]` The content-hash dedupe key buckets on `event.observed_at` by hour (`ingest.py:253`), so
  connectors that put a poll time there bucket by poll hour and connectors that put a source time
  there bucket by source hour. This change leaves that alone.
- `[Observed]` The ingestion event list keyset-paginates on `(received_at DESC, id DESC)` and filters
  on `received_at` (`src/butlers/api/routers/ingestion_events.py:195-228`); other reads order on
  `received_at` too. No dashboard read orders on `event.observed_at`.
- `[Unknown]` `roster/switchboard/api/router.py:1132` orders by `observed_at ASC`; the table and column
  it reads were not verified.

## What each connector has in hand

`observed_at` column: what the connector puts there today. Source time in hand: a stated time the
payload already carries. Cursor: what is persisted via `switchboard.connector_registry.checkpoint_cursor`
(`cursor_store.py`) unless noted.

| Connector | `observed_at` today | Source time in hand (unused for the clock) | Cursor |
| --- | --- | --- | --- |
| Gmail | source-stated (`internalDate`, `gmail.py:2552`; `now()` on parse error `:2581`) | `internalDate` is used; `Date` header parsed at `:2555` | `{history_id, last_updated_at}`, opaque sequence token |
| Telegram bot | receipt (`now()`, `telegram_bot.py:1916`) | `message.date` inside the raw update | `{last_update_id}`, sequence |
| Telegram user client | receipt (`now()`, `:2004`; batch flush `:1203`) | Telethon `message.date` (read at `:686`, `:1124`) | `{last_message_id, telethon_update_state{pts,qts,date,seq}}`, sequence plus opaque |
| WhatsApp user client | mixed: bridge `timestamp` for single events (`:2038-2045`), `now()` for batches (`:1879`) | per-message timestamps collected at `:1914`, `:1950` | `{last_event_id}`, opaque bridge id |
| Discord user | receipt (`now()`, `discord_user.py:1081`) | message `timestamp`; snowflake encodes creation time | per-channel snowflake map |
| Home Assistant (WebSocket) | source-stated (`time_fired`, `home_assistant_envelope.py:241`) | n/a | `{last_event_ts, ...}`, a time |
| Home Assistant (REST fallback) | poll time as synthetic `time_fired` (`home_assistant_rest.py:245-281`) | `last_changed`, `last_updated` | same checkpoint |
| Steam | poll (`steam.py:1156`) | achievement `unlocktime` (carried in payload, `:560`) | `connectors.steam_cursors` state snapshot and hash (not `cursor_store`) |
| Spotify | poll (`spotify.py:2487`) | per-item `played_at` (parsed at `:2767`) | `after` epoch ms, a time |
| OwnTracks | receipt (`now()`, `owntracks.py:1286`) | device `tst` (epoch seconds, used in the idempotency key) | `str(tst)`, a time |
| ActivityWatch | poll (`activitywatch.py:904`) | event `timestamp` and `duration` | ISO timestamp, a time |
| Live listener | source-stated (`offset_ts`, `live_listener/connector.py:550`) | n/a | `{last_utterance_ts,...}` session state only |
| Google Calendar | poll (`google_calendar.py:526`) | event `updated` (in the idempotency key) | `syncToken`, opaque |
| Google Drive | poll (`google_drive.py:664`) | `modifiedTime` (in the idempotency key) | `page_token`, opaque |
| Google Health | poll (`google_health.py:1951`) | record interval times (referenced by filters at `:322`, `:394`) | `record_id`, opaque identity |

Not examined: `bridge_manager.py`, `owner_outbound_events.py`. Reading of the table: twelve of fifteen
rows already hold a stated source time they do not use for `observed_at`; three already put one there
(Gmail, Home Assistant WebSocket, live listener).

Every connector heartbeat proves process liveness, the connector's self-reported health and a copy of
its cursor (`heartbeat.py:137,339-341`). It does not prove that source events were seen.

## Decisions

[decision] Add a new nullable `occurred_at` rather than redefine `observed_at`. `observed_at` has
two incompatible meanings across connectors and feeds the dedupe bucket; redefining it would change
dedupe for connectors that adopt. Cost: three clocks (source, connector-observed, receipt) instead of
two. Reversible: yes, the fields are optional.

[decision] Closed provenance vocabulary of three values and a two-value precision. `source_stated`
and `receipt_only` come from the move; `connector_derived` covers an identifier-derived time such as a
Discord snowflake. `day` precision exists for date-only provider values (Google Health daily
summaries). Reversible: adding a value later is a spec change, removing one is not.

[decision] Three new capabilities instead of editing `connector-base-spec`, to avoid the overwrite
collision with the `generalist-lead-focused-delegation` MODIFIED copy of `ingest.v1 Envelope Contract`.

[decision] Coverage evidence is limited to three kinds. `time_cursor` exists only for connectors
whose cursor is itself a time (Home Assistant, Spotify, ActivityWatch, OwnTracks). The
opaque-token and identity-cursor connectors contribute `gap` evidence (`cursor_reset`,
`connector_offline`) and otherwise read `unknown`. This is deliberately conservative: it yields few
`covered` spans and no false all-clear.

[decision] Case key `source-lapse/<connector_type>/<endpoint_ref>` with an opaque `endpoint_ref`,
because Gmail's `endpoint_identity` is an email address and the key is readable in the dashboard.
Recommended derivation: the first 12 hex characters of SHA-256 over the identity; owner may choose
another stable opaque form.

[decision] Slice D is `v1-reserved`, slices A and B `v1-mandatory`: A and B correct a standing
dishonesty (a poll time presented as an event time, a cursor presented as coverage) with data already
held; D opens cases and needs bu-h40h2b.8's case/situation owner to approve the key and state use
first.

## Slice C: the backfill door

Specified as absent, with a fixed disabled reason if a surface renders an affordance anyway
(`REQ-connector-event-clock-007`). Reason: the owner-vetted posture A excludes provider calls and
quota spend, and a replay would also need an eligible-gap contract, a quota posture and cancellation
semantics that do not exist. Existing connector backfill paths are listed so no reader assumes they
are reachable from coverage. An owner-triggered backfill would be its own decision.

## Persistence shape (not a migration)

Slice A needs three columns beside `received_at` on `public.ingestion_events` (REQ-003) and the
matching write in the ingest path. Slice B needs a place to remember gaps, because a gap cannot be
reconstructed later from the current cursor. Recommendation: persist only assertions that cannot be
recomputed (`gap`, `cursor_reset`, `connector_offline`) and compute `time_cursor` spans on read. Owner
question below. No migration is written here.

## Open questions for the owner

1. Is persisting derived gap assertions acceptable under posture A ("no new collection")? They are
   derived from heartbeat and cursor records already held, not collected from a provider.
2. Does the situation owner (bu-h40h2b.8) accept the `source-lapse/...` key and the
   `source_silent` / `source_recovered` evidence kinds?
3. Which outcome label should a recovered lapse case close with? `fleet-case-file` requires a
   non-blank outcome and defines only `lapsed` by name.
4. Should the occurred-basis presentation be offered at all in the first implementation, or should
   `occurred_at` be stored and shown as a column only?
