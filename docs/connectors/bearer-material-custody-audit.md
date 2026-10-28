# Bearer material custody audit

Audit date: 2026-10-14. Source tree: **`fb0dad17316c0b20869e7b4de97ea0ba97fbb1a6`**.
Issue: `bu-q7vx1q.22`, following `.2` / PR4345. This is the adopted source/synthetic
audit tranche. It makes no product, runtime, credential, permission or historical-data
change. No live inbox, connector store, credential, message or log was read; no connector,
model, bridge, purge or deployment was activated.

The normal Switchboard ingestion scrub is present. It does **not** establish custody
for filtered replay copies, pre-ingest discretion logs/prompts, alternate inbox writers,
legacy rows or every evidence/cache store. Synthetic execution proves retained bearer
text at several of those seams. Database durability, actual-role access and vendor
custody remain **UNKNOWN** where the required harness was unavailable.

## Authority and verdicts

The binding requirement is [connector-base-spec: Bearer material never persists](../../openspec/specs/connector-base-spec/spec.md#requirement-bearer-material-never-persists),
with the [Gmail](../../openspec/specs/connector-gmail/spec.md) and
[Telegram user-client](../../openspec/specs/connector-telegram-user-client/spec.md) counterparts.
It covers inbound one-time codes, password-reset links, magic links and Telegram login
codes in persisted payloads; typed kind/domain/time placeholders preserve the observation.
[Security constraints](../../about/heart-and-soul/security.md),
[vision rules](../../about/heart-and-soul/vision.md), the
[Switchboard manifesto](../../roster/switchboard/MANIFESTO.md),
[schema topology](../data_and_storage/schema-topology.md),
[credential contract](../data_and_storage/credential-store.md),
[session lifecycle](../runtime/session-lifecycle.md) and
[response conventions](../api_and_protocols/response-conventions.md) govern the other boundaries.
Dashboard audit access is not a content scrub. Actor attribution, fact-source authority,
identity, OAuth, source metadata, policy decisions and transport authentication are not
changed or newly authorized here.

The existing [filtered-preview contract](overview.md#filtered-preview-bearer-redaction)
scrubs **only `subject_or_preview`**. Its label-safety rule explicitly preserves other
detector callers, source metadata and replay payloads. It cannot authorize a full-payload
rewrite, replay-data loss, new failure policy, binary sanitization or live cleanup.
Each corrective packet must adopt its exact owned behavior first.

| Verdict | Meaning and evidence limit |
| --- | --- |
| SAFE-SCOPE | The named content-free projection or tested scrub seam is safe within its stated scope. It does not certify upstream metadata, other fields, deployed state or all artifacts. |
| GAP | Source tracing or synthetic execution demonstrates text can reach the stated sink without the governing scrub. A bind is evidence of submitted values, not proof a database committed them. |
| UNKNOWN | Required contract, real harness, dynamic or vendor boundary is missing. The named evidence target must resolve it; UNKNOWN never means safe. |
| OUTSIDE | The exact path is not an inbound message-content store under this requirement, such as transport credentials or outbound delivery. Its own contract still applies. |

Source-only conclusions below are marked `S`; executing synthetic conclusions are `E`.
All queues that temporarily receive raw messages are custody locations, but volatile
reception alone is not a violation of the persisted-payload rule. Prompt/log egress and
durable writes are evaluated separately. Provider/sender/domain labels are untrusted
metadata, not verified sender identity; the detector's link-host hint is not a security
sensor's sender-domain contract. `.20`'s observation policy remains separately gated.

## Inventory method and entrypoint closure

The inventory includes every implemented source adapter under `src/butlers/connectors`,
its helper modules, `pyproject.toml` script registrations and the module commands in
`docker-compose.yml`. Fourteen adapter families are represented below. Home Assistant's
WebSocket, REST fallback and recovery helpers are one family; live listener is a package.
Console scripts register Gmail, Google Calendar, Google Drive, Steam, Telegram user-client
and Discord user (the Discord connector was retired after this audit, bu-lsxqb0.25; its
rows below are historical). Every listed adapter also has a module/run entrypoint, except that the
live-listener entrypoint is its package `__main__.py`. This inventory describes source
entrypoints, not a claim that every one is deployed or enabled.

Read discovery covered qualified/unqualified `message_inbox`, `raw_payload`,
`normalized_text` and written descendants `decomposition_output`, `response_summary`
and `dispatch_outcomes`, plus delegating wrappers across `src/`, `roster/`, `scripts/`
and migrations. AST traversal
located 167 literal references, including comments/docstrings and DDL; they were traced
to the actual owners rather than treated as 167 readers. The count establishes literal
inventory only, not content classification: `ingestion_event_get_inbox_lifecycle`
selects unrestricted text-bearing decomposition as well as lifecycle state. F-strings,
table constants, callers and sinks were inspected. Searches of SQL/Go/shell source in
those roots and the WhatsApp bridge found no additional `message_inbox` query implementation. Migration
functions create/ensure partitions and grants; they do not introduce an additional text
view or text-reading stored function at this SHA. Dynamic SQL outside the checked-in
source, installed plugins, deployed catalog drift and external libraries remain UNKNOWN.

Reproduce source discovery from the stated source tree (never against a live database):

```bash
git rev-parse HEAD
rg --files src/butlers/connectors
rg -n 'project.scripts|connector =' pyproject.toml
rg -n 'entrypoint:.*butlers.connectors' docker-compose.yml
rg -n 'message_inbox|normalized_text|raw_payload|decomposition_output|response_summary|dispatch_outcomes' src roster scripts alembic --glob '*.py' --glob '*.sql'
rg -n 'Queue|deque|heap|buffer|cache|INSERT INTO|save_cursor|full_payload|drain_replay_pending' src/butlers/connectors whatsapp-bridge/internal
```

Tests, type models and docstring references are not runtime readers. The referenced
structured dossier `docs/redesigns/2026-10-03-jarvis-pursuit-data.json` is absent at this
SHA; the [Markdown dossier](../redesigns/2026-10-03-jarvis-pursuit.md) is available.
No missing dossier evidence was reconstructed. Inactive/spec-only providers are not
invented as implemented adapters; a future adapter or changed source invalidates the
affected inventory and needs a fresh custody trace.

The connector-prefixed spec inventory contains 13 adapter capabilities plus six
cross-cutting contracts: base, filtered events, lifecycle ceremony, replay idempotency,
replay queue and state aggregates. Those six are not extra provider implementations.
Discord's capability was named `connector-discord` (retired, bu-lsxqb0.25); WhatsApp has an implementation and
[connector documentation](whatsapp.md), but no dedicated `connector-whatsapp` baseline
spec at this SHA. It is covered by the base/credential contracts and the matrix, not
excluded because its dedicated capability file is absent.

## Connector custody matrix

For each row, normal receive/build/filter/submit ends at the Switchboard `ingest` tool
and accepted inbox. `F` means the shared filtered-buffer/replay seam in the next table;
`C` means the shared cursor/registry seam. Those shared rows supply role/schema,
failure and replay detail for every caller. Connector DB pools use the connector-role
infrastructure; role names in source are not proof of the deployed catalog or RLS.
All bearer outcomes cite the binding requirement above; evidence-store field ambiguity
is retained in G4 instead of inventing a cleanup policy.

| Adapter / source entrypoint | Volatile pre-submit custody and content | Durable custody / boundary | Scrub, retry or rehydration; failure disposition | Verdict and next evidence |
| --- | --- | --- | --- | --- |
| ActivityWatch: `activitywatch.py::run_activitywatch_connector` | Poll event dictionaries and `_process_window_event` hold title/URL; `build_activity_envelope` excludes both, emits coarse class/duration and full-tier app process name only. F holds filtered rows. | `persist_activity_event` -> `connectors.activitywatch_events` title and `raw_payload` **after successful ingestion**; C stores timestamp checkpoint. | Evidence insert uses idempotency key/`ON CONFLICT DO NOTHING`; title/raw bind is not scrubbed. Filtered record keeps raw `{}`. Evidence failure is nonfatal. Checkpoint advances through `_save_checkpoint`. | GAP E: evidence bind retains fixture, with explicit forensic-fidelity contract conflict in G4. SAFE-SCOPE S: title/URL excluded from normal envelope and timestamp cursor. Actual evidence readback/role/retention UNKNOWN. |
| Retired (bu-lsxqb0.25): Discord user: `discord_user.py::run_discord_user_connector` | Gateway dispatch event; `_channel_checkpoints`, gateway session ID; `_normalize_to_ingest_v1`; F. | F error row `_process_dispatch_event` retains `event_data`; C channel message IDs. | Normal envelope has no pre-submit scrub; ordinary drops use raw `{}`. Error full payload includes event dictionary and exception string; F replay re-emits it. Operational gateway IDs are not message text. | GAP S: error copy and normal text egress; GAP E at F. SAFE-SCOPE S: ID checkpoint. Actual caller positive/negative/error harness UNKNOWN, G1/G3. |
| Gmail: `gmail.py::main` | `_notification_queue` contains Pub/Sub notifications/history ID/mailbox, not fetched message body; fetched MIME, attachment resolution and policy processing hold text; `_sent_ids_cache` holds IDs; F. | F drops use raw `{}` with no body text; error payload contains only message ID. C `GmailCursor` history/backfill progress. Attachment blob custody is separate. | `_build_ingest_envelope` scrubs full and metadata-only subject envelopes. `_ingest_single_message` filtered-drop logs (live and backfill, six lines) now log the message id, not the subject (`bu-rob5w5`); the preview is scrubbed separately. Poll/event processing flushes F and drains replay; cursor reloads source history. | SAFE-SCOPE E: normal full scrub (named test); metadata-only scrub S. SAFE-SCOPE S: filter-drop logs now name the message id, not the subject (`test_filter_drop_logs_message_id_not_subject`); F error_detail is not covered by content-free raw. SAFE-SCOPE S: body-free notification/cursor. Attachment/provider custody UNKNOWN, G1/G3/G4. |
| Google Calendar: `google_calendar.py::main` | `_upcoming_events` contains event/description; `build_event_envelope` and `_build_ingest_envelope` normalize text; F. | F blocked rows raw `{}` **plus normalized_text**; submission errors retain raw/text. C sync token. | Neither builder applies bearer scrub. `_process_event` retains text even beside preview-only comments. Retry/replay goes through submit and central scrub only after the retained copy. | GAP S: F payload/error content; GAP E at shared F. SAFE-SCOPE S: sync-token cursor only. Real event error/description harness UNKNOWN, G1. |
| Google Drive: `google_drive.py::main` | `_metadata_cache`; `_pending_batch` `_PendingChange` includes filename/owner/time; batch digest contains names. | `_save_metadata_cache_to_store` writes names to `switchboard.connector_registry.settings.metadata_cache`; F normalized filename despite raw `None`; C page token. | `_load_metadata_cache_from_store` restores the cache; submission/filter builders do not scrub names. Metadata-only content remains text-bearing. Cache/save errors are nonfatal and logged. | GAP S: F normalized text lacks scrub. UNKNOWN: governed filename-cache bearer scope/fidelity and actual readback, G4. SAFE-SCOPE S: page-token projection. |
| Google Health: `google_health.py::main` | Account/resource poll records, normalization and sleep/workout/daily builders; submit semaphore; F. | F blocked raw `{}` **plus normalized_text**; submission error retains entire envelope. C per-resource cursor. | `_submit_envelope` filters before MCP submit; no bearer scrub in these builders. Most fields are measurements/time, but free-text/provider fields require schema proof. Replay uses `_submit_to_ingest_direct`. | GAP S: generic retained text envelope when populated; GAP E at F. UNKNOWN: closed field population and numeric-only claims, G1/G4. SAFE-SCOPE S: cursor projection. |
| Home Assistant: `home_assistant.py` module runner, split helpers | `HAWebSocketClient` pending request futures/event queue; `HAEventReorderBuffer` heap; significance/state caches; REST poll/recovery response dictionaries. State/attributes/friendly names can carry text. | `home_assistant_filter.py::ha_full_payload` -> F raw event; `persist_ha_history` -> `connectors.home_assistant_history` state/attributes for **person** events after successful submissions; C HA event/recovery checkpoints. | `HAFilterPipeline`, envelope/wellness/statistics helpers build/route data without a bearer text scrub. Failed submission leaves checkpoint unchanged; reorder, WS reconnect and REST recovery can replay. Person-history scope must not be generalized to every sensor. | GAP S: F raw content lacks scrub. UNKNOWN: exact state/attribute custody, post-submit evidence and real restart/roles, G1/G4. SAFE-SCOPE S: typed checkpoint IDs/times. |
| Live listener: `live_listener/__main__.py` -> connector runner | `audio.RingBuffer` PCM; VAD utterance bytes; transcription in-memory WAV/stream; `prefilter._dedup_window` normalized transcripts; per-mic sessions; discretion context. | F ignored records use raw `{}` and preview; C session/time checkpoint. Python transcription source uses in-memory audio packaging, not a checked-in audio spool writer. Remote transcription/vendor/audio custody is separate. | Text goes to prefilter/discretion before central submit scrub. `_record_discretion_ignore` does not retain body in F; preview scrub applies. Audio ingestion is not a text-detector guarantee. | SAFE-SCOPE E: discretion prompt/log/window (the live-listener caller uses the default hints; see the discretion row, `bu-rob5w5`); caller order S. SAFE-SCOPE S: body-free filtered record/checkpoint; producer logs (`bu-n356fz`): `_process_segment` INFO transcription and DEBUG submitted-utterance lines, and the Wyoming low-confidence discard line, now log mic, confidence and character count, never transcript text (`test_process_segment_logs_ids_and_length_not_transcript`, `test_wyoming_discard_log_has_length_not_transcript`). UNKNOWN: binary/audio/provider persistence and real caller harness, G3/G4. |
| OwnTracks: `owntracks.py::run_owntracks_connector` | MQTT/webhook body dictionaries, per-device handling/registry state; waypoint/description/extra fields need closed schema proof; F. | `persist_location_point` -> `connectors.owntracks_points.raw_payload` **after successful ingestion/checkpoint**; F retains body; C device progress. | Policy-block handling writes F raw body before envelope/central scrub. Location evidence inserts use idempotency and preserve accepted payload fields under the owning fidelity spec; evidence failure is nonfatal. | GAP S: F raw body; UNKNOWN: evidence field-custody conflict and real persisted readback, G1/G4. Numeric location alone is not raw-dictionary safety. |
| Spotify: `spotify.py::main` | `_context_name_cache`; session/spoken trackers; `_pending_closed_track_plays`; poll/client response metadata and names; F. | `connectors.spotify_listening_sessions`, `spotify_spoken_sessions`, `spotify_track_plays`; F blocked raw `{}` **plus normalized_text**, errors full envelope; C session progress. OAuth token storage is outside inbound text scope. | Session/track/spoken writers retain differing metadata populations; spoken writer explicitly omits raw payload. Submit error F copy still retains text; no general builder scrub. Closed plays retry/dedupe by their own keys. | GAP S: F text/error content. UNKNOWN: evidence/name-field custody and codec/roles, G1/G4. OUTSIDE: credential refresh. |
| Steam: `steam.py::main` | Poll response objects, normalized summary/app/profile names, retry state; F. | `connectors.steam_play_history` app name/time, `connectors.steam_cursors` typed cursor state; F `_maybe_submit` raw/text, poll-error records data-type/status/error. | `_maybe_submit` filters before submit without bearer scrub. Play-history upserts accumulate deltas; legacy account-null upsert is distinct. Cursor refresh/dedupe is not sanitization. | GAP S: message-bearing F payload. UNKNOWN: closed evidence-name population and real codec/readback, G1/G4. SAFE-SCOPE S: numeric/time-only cursor fields. |
| Telegram bot: `telegram_bot.py::run_telegram_bot_connector` | Update dictionary, normalization, attachment download, `_media_ref_cache`; F. | F normal drops/attachment failure records raw `{}`; processing error retains entire update. `switchboard.media_refs` stores attachment refs; blob bytes separate. C update IDs. | `_normalize_to_ingest_v1` lacks pre-submit text scrub. `_persist_media_ref` caches refs; error path records raw update before central scrub. Replay uses F. | GAP S: error update; GAP E at F. SAFE-SCOPE S: body-free drop rows and update-ID cursor. UNKNOWN: attachment binary/object-store retention and executing caller/error test, G1/G4. |
| Telegram user-client: `telegram_user_client.py::run_telegram_user_client_connector` | `_chat_buffers` raw Telethon messages, history/participant lookup caches, batch locks, discretion on original text; F. | F ordinary/batch drops use raw `{}`; `_process_message` error uses raw `message.to_dict`; C message progress. StringSession/native transport session is credential custody. | `_normalize_to_ingest_v1` **and `_build_batch_envelope`** call `scrub_envelope`; this occurs after original-message discretion and cannot protect earlier logs/error copies. Restart fetches source history. | SAFE-SCOPE E: normal service-sender scrub; batch scrub S. GAP S/E: prior discretion/error copy. SAFE-SCOPE S: checkpoint IDs. OUTSIDE: StringSession credentials; native library store UNKNOWN, G1/G3/G4. |
| WhatsApp user-client: `whatsapp_user_client.py::run_whatsapp_user_client_connector` | SSE bridge events, `_chat_buffers`, per-chat lock, history resolution/batch envelope and discretion; F. Bridge's `replayEvents`/`replayEventIDs` retain cloned text events. | Python F batch drops use raw `{}`; C timestamp/ID state. Bridge bounded replay/handoff is **memory**, not a source-proven durable text store. `messenger.whatsapp_sessions.session_data` and Whatsmeow native auth store have credential contracts. | Batch/single envelopes do not scrub text before discretion/submit. Bridge normalizes live/history and hands off replay; existing bounded/dedup tests execute that behavior, not bearer sanitization. | GAP S/E: discretion text egress; GAP S: pre-submit text. SAFE-SCOPE S: body-free filtered row/checkpoint. UNKNOWN: native library message persistence/retention; OUTSIDE: auth-key/session data, G3/G4. |

### Every filtered record producer

AST/caller tracing finds **36 production `record(full_payload=...)` callsites**. The
line numbers below refer to the recorded source SHA. Grouped rows cover every branch;
`SAFE-SCOPE` refers only to the omitted body subtree, not error strings or identity
labels. All use shared F flush/replay semantics in the next table.

| File / symbol / record lines | Full-payload body shape | Verdict / disposition |
| --- | --- | --- |
| `activitywatch.py::_process_window_event` 1355 | raw `{}`, no normalized body | SAFE-SCOPE S for body; evidence separately G4 |
| Retired (bu-lsxqb0.25): `discord_user.py::_process_dispatch_event` 832, 875, 910; error 960 | Drops raw `{}`; error raw event dictionary | Drops SAFE-SCOPE S; error GAP S, G1 |
| `gmail.py::_ingest_single_message` 2211, 2247, 2284; error 2362 | Drops raw `{}`; error message-ID-only raw | SAFE-SCOPE S body; raw subject log G3, generic error_detail G1 |
| `google_calendar.py::_process_event` 1486, 1523; `_submit_envelope` 1603 | Drops normalized_text even with raw `{}`; error raw/text | GAP S, G1 |
| `google_drive.py::process_change` 1710 | normalized filename, raw `None` | GAP S text, G1; cache contract G4 |
| `google_health.py::_submit_envelope` 2122, 2175 | Drop normalized_text/raw `{}`; error entire envelope | GAP S generic text storage; closed numeric/text population UNKNOWN, G1/G4 |
| `home_assistant_filter.py::record_domain_excluded` 302; `record_insignificant_delta` 345; `record_discretion_ignore` 390; `record_global_skip` 444 | `ha_full_payload` retains event/state/attributes | GAP S generic raw text; exact fields UNKNOWN, G1/G4 |
| `live_listener/connector.py::_record_discretion_ignore` 749 | raw `{}`, no transcript body | SAFE-SCOPE S body; prior discretion G3 |
| `owntracks.py::_process_webhook_event` 1354 | raw webhook body | GAP S; field/fidelity gate G1/G4 |
| `spotify.py::_submit_envelope` 3071, 3128 | Drop normalized_text/raw `{}`; error entire envelope | GAP S, G1 |
| `steam.py::_poller_loop` 992, 1054; `_maybe_submit` 1599 | Error data-type/status/exception text; filtered raw/text | GAP S message-bearing text/error, G1 |
| `telegram_bot.py::_process_update` 930, 966; error 1008; `_normalize_to_ingest_v1` 1875 | Drops/attachment record raw `{}`; error raw update | Drops SAFE-SCOPE S; error GAP S, G1 |
| `telegram_user_client.py::_record_batch_filtered_event` 861; `_process_message` 1295, 1332, 1402; error 1456 | Drops raw `{}`; error `message.to_dict()` | Drops SAFE-SCOPE S; error GAP S, G1 |
| `whatsapp_user_client.py::_record_batch_filtered_event` 1636 | raw `{}`, no batch body | SAFE-SCOPE S body; prior discretion G3 |

## Shared seams, durable stores and failure matrix

Source anchors: [filtered buffer](../../src/butlers/connectors/filtered_event_buffer.py),
[detector](../../src/butlers/ingestion_bearer_scrub.py),
[discretion](../../src/butlers/connectors/discretion.py),
[cursor store](../../src/butlers/connectors/cursor_store.py),
[MCP client](../../src/butlers/connectors/mcp_client.py),
[Switchboard ingest](../../roster/switchboard/tools/ingestion/ingest.py).

| Caller / exact seam | Content, lifetime and role boundary | Scrub / downstream and replay | Failure, concurrency and idempotence | Verdict / correction |
| --- | --- | --- | --- | --- |
| All F callers: `FilteredEventBuffer.full_payload`, `record`, `flush` | Volatile `_rows` -> `connectors.filtered_events`; preview, full JSONB payload and error_detail are different columns. Connector writer pool. | `record` scrubs preview only; JSON-safe serialization preserves full payload; `flush` binds it unchanged. Source/event/sender/control remain intact. | Partition creation outside transaction; one executemany; failed flush clears/drops rows; publication failure after write does not retry committed rows. Actual PG/codec readback UNKNOWN. | SAFE-SCOPE E: preview. G1 (`bu-v3pw5l`) fix: SAFE-SCOPE E for new rows, since `record` scrubs the envelope with `scrub_envelope` and `error_detail` with the same hints; a detector failure withholds text and keeps status (`test_record_scrubs_payload_and_error_detail`, `test_record_detector_failure_withholds_text_without_changing_status`, `test_record_keeps_ordinary_numbers_byte_identical`). Rows written before the fix are not rewritten. |
| F replay: `drain_replay_pending`, `_sanitize_replay_payload` | Durable filtered payload -> process memory -> `submit_fn` -> central ingest; accepts JSON object and legacy string. | Sanitizer adapts identities/nulls/drop markers, **not bearer text**. Status/error update follows submit. | Up to 10 rows; `FOR UPDATE SKIP LOCKED` within one transaction. Crash/acknowledgement ambiguity may re-submit; malformed row marks failure. Locks/exact real-role outcomes UNKNOWN without PG. | New rows replay placeholder text with unchanged identity (`test_scrubbed_row_replays_with_placeholder_and_unchanged_identity`); legacy rows written before the G1 fix still replay raw text. UNKNOWN: concurrent/restart/commit-ack durability. G1. |
| C: `cursor_store.load_cursor/save_cursor`; HA/live/Gmail typed wrappers | `switchboard.connector_registry` cursor/settings. Source callsites serialize IDs, timestamps, progress; cursor_store itself accepts an arbitrary string. | Load restores progress; no content scrub. Drive metadata-cache is a separate text-bearing settings writer, not a cursor-safety exception. | Upsert preserves runtime-instance roles on conflict; operational_role controls liveness interpretation. Failed save/restart may repeat source work; bearer/idempotence not inferred from cursor keys. | SAFE-SCOPE S for enumerated typed progress fields. UNKNOWN for future/arbitrary callsites and real catalog/roles. Drive G4. |
| `DiscretionEvaluator.evaluate`, `ContextWindow`, `_build_user_prompt`; `discretion_dispatcher.call` | Current/history text retained in bounded volatile context; prompt sent to runtime adapter; evaluator logs first 500 prompt characters. | No text scrub before window, prompt or evaluator log. Dispatcher avoids prompt/identity logs of its own; that does not undo evaluator logging or prove provider retention. | Window appended even on channel/weight/group bypass; timeout/error uses existing weight failure decision. No live/paid call in audit; fake dispatcher only. | G3 (`bu-rob5w5`) fix: SAFE-SCOPE E at window/prompt/log; `evaluate` scrubs text first, with provider/aggressive hints passed by the Telegram user-client (batch and live) and WhatsApp callers; live-listener and Home Assistant use the defaults (no hint), and the live-listener transcript logging is tracked separately in `bu-n356fz`, and a detector failure substitutes `[content withheld]` (`test_evaluator_withholds_bearer_text_from_prompt_log_and_window`, `test_evaluator_bypass_paths_keep_verdicts_and_store_scrubbed_entry`, `test_live_discretion_passes_aggressive_hint_for_service_sender`). UNKNOWN actual provider/session logging. |
| `mcp_client.CachedMCPClient` | MCP connection/request arguments in memory; cached transport connections. | No independent bearer transform. Ultimate ingest may scrub after earlier stores. | Retry/timeout/lost acknowledgement require idempotency at receiver. No checked-in durable payload spool in this helper; transport/vendor internals UNKNOWN. | SAFE-SCOPE S: no local durable spool found; UNKNOWN vendor buffering. G4. |
| `bridge_manager`, WhatsApp `internal/api/server.go::RecordHistoryEvent`/replay handoff | Manager tracks process/status/recovery; native API queue clones message events in bounded memory; keys dedupe by chat/message. | Native event/history normalization retains text; Python caller later submits. Session SQL store is auth/session data, not identified as inbound message text. | Existing five replay/handoff tests pass, proving bounds/dedup/order only. Native library durable message population/retention UNKNOWN; no auth-store inspection. | UNKNOWN custody beyond owned bridge; OUTSIDE transport-auth credentials. G4. |
| `owner_outbound_events.record_owner_outbound_point` | `connectors.owner_outbound_events`: hashed dedup key, channel, endpoint and occurred_at; no body. | Metadata-only producer used by personal-client outbound detection. | `ON CONFLICT DO NOTHING`; failures nonfatal. Does not imply other outbound stores safe. | SAFE-SCOPE S: content-free exact columns. |
| `ingest.py::_scrub_bearer_material`, `ingest` | Sanitized envelope `payload`/normalized_text -> accepted `message_inbox`; Switchboard schema/runtime role. | Central scrub stores typed artifacts; detector exception converts to metadata-only (`payload.raw` null). Session helper also scrubs text before enqueue in core_tools. | Existing content and metadata-invariance tests pass. Dedupe/partition transaction behavior is separate from detector proof; no real PG proof claimed here. | SAFE-SCOPE E/S for named current ingest content helper. Other writers/readers below remain independent. |
| `MessagePipeline._accept_ingress` | Alternate `message_inbox` writer: top-level `raw_payload.content`, metadata and normalized_text. | This writer now scrubs content and normalized_text with `scrub_stored_record` before the INSERT. `process()` also scrubs `message_text` once (placeholder, or `[content withheld]` on detector failure) so the routing prompt, INFO preview, response summary and dead-letter cc_output use the scrubbed text; the dedupe key now derives from the scrubbed text (`bu-y0cll7`; historical rows tracked in `bu-tnm8zn`), and `process()` with an existing inbox id was already scrubbed upstream (`test_process_routes_scrubbed_text_and_withholds_it_on_detector_failure`). | Partition ensure autocommit then advisory-lock/dedupe transaction; duplicate branch reuses row. | G2 (`bu-ksao0y`) fix: SAFE-SCOPE E for new rows; `scrub_stored_record` runs before the dedupe transaction and the INSERT binds scrubbed content/text, a detector failure leaves no row (`test_accept_ingress_binds_scrubbed_text_and_keeps_dedupe_reuse`). UNKNOWN real persistence/dedupe roles. |
| `pipeline.py::_normalize_decomp_excerpts` / `_normalize_decomp_signal`; `MessagePipeline.process` -> `_update_message_inbox_lifecycle` | Authoritative conversation source `excerpts[].text` and model-returned `tool_args` -> `message_inbox.decomposition_output`; lifecycle UPDATE also binds `response_summary` and dispatch outcomes. | Normalizer copies source excerpts and preserves arguments, without bearer scrub; successful process builds full signals and binds them with outcomes. `response_summary` can be `cc_output[:500]`, not only fixed status copy. | Atomic UPDATE is a submitted value, not commit proof. No runtime/provider invocation or real DB write in probe. Real PostgreSQL/current codec/roles/commit/concurrency UNKNOWN. | GAP S/E: decomposition normalizer/UPDATE and submitted summary bind retain synthetic text. G2 `bu-ksao0y`; excerpt/storage fidelity contract must be adopted before correction. |
| `operator/controls.py::cancel_request`, `abort_session`, `force_complete` summary writes | `message_inbox.response_summary` receives operator-reason-derived strings; other calls use fixed status text. | Written text descendant, not a no-text lifecycle projection. No checked-in reader of response_summary was found at this SHA; no reader is invented here. | Actual caller, stored population and durable/role effect remain UNKNOWN. User-authored operator reasons are not automatically inbound bearer content. | UNKNOWN field/caller custody, G2; pipeline summary bind above is separately GAP E for contaminated synthetic input. |
| `dead_letter.capture_to_dead_letter`, `replay_dead_letter_request` | Original content -> `dead_letter_queue.original_payload`; replay inserts `switchboard.message_inbox` content/text; operator audit stores lineage. | Capture and replay scrub `original_payload` (`content`, `message_text`, `payload` subtree) before their INSERT; legacy strings are parsed first; operator reason/exception still need their own audit contract. | Replay row lock, eligibility/already-replayed guards, nested savepoint; failed replay status written outside rolled-back savepoint. Real transaction/codec/readback UNKNOWN. | G2 (`bu-ksao0y`) fix: SAFE-SCOPE E/S for new rows; capture and replay both scrub `original_payload` before their INSERT (`test_dead_letter_capture_stores_scrubbed_original_payload`, `test_dead_letter_replay_scrubs_legacy_raw_payload`); replay status/lineage unchanged. Dead letters captured before the fix are scrubbed only when replayed. |
| `notification.deliver::_write_outbound_message_inbox` | Outbound notify message in inbox content/normalized_text; source provenance points to delivery. | No bearer scrub. Direction is **outbound**, so inbound requirement alone does not authorize discarding delivery text. | Recovery-specific early exit/lineage; histories can read outbound text. | OUTSIDE inbound writer contract; reader compatibility/owner scope UNKNOWN, G2/G4. |
| `scrub_stored_record`, migration `040_scrub_bearer_material::_scrub_row` | Historical row content helper; migration reads `raw_payload`, normalized_text and writes only changed rows. | Scrubs `raw_payload.payload`, normalized_text and a legacy top-level `content` string. Does not rewrite source metadata. | Scrub idempotent. Applied migration state/historical values not read; no audit backfill/purge run. Irreversible material loss needs separate live authority. | G2 (`bu-ksao0y`) fix: SAFE-SCOPE E, the helper now scrubs a legacy top-level `content` string (`test_stored_record_scrubs_legacy_top_level_content`); metadata/content contract tested. Readers inherit the write-boundary invariant for new rows; historical alternate-writer rows are not rewritten. UNKNOWN historical population/applied state. |

Helper-only exclusions are source-backed: `db_role`, `registry_roles`, `metrics`,
`heartbeat`, `health_socket` and live-listener metrics/config own role, status or count
metadata, not a checked-in message replay store. `gmail_policy`, `known_contact_state`
and contact-weight resolution cache policy/identity/weights, not bodies; policy evaluation
arguments still see text in the adapter path. `google_health_client` and `spotify_client`
are source HTTP/auth transports. `telegram_user_client_consent` stores consent state.
Home Assistant's checkpoint/envelope/filter/pipeline/REST/statistics/wellness helpers
and every live-listener helper are included in their family rows. `__init__`, README
and entrypoint wrappers introduce no additional custody. These exclusions do not
certify arbitrary metadata, credentials, external SDK storage or future callers.
`discretion_dispatcher._adapter_cache` / `_adapter_cache_key` cache runtime adapter
objects, not an identified message backlog; actual provider retention remains UNKNOWN.

## Every inbox reader and writer prerequisite

The source inventory covers discovered runtime SQL owners and delegating text readers
at this SHA, including full text descendants rather than only original body columns.
Most text readers trust previously stored content; a safe current central writer cannot
certify legacy or alternate-writer rows. Evidence below intentionally preserves this gap.
No source reference establishes permission to change raw API shapes, accepted-source
digests, privacy/authorization boundaries or outbound delivery.

| Reader / symbol and source | Content read / caller -> sink | Independent scrub and verdict | Evidence target / disposition |
| --- | --- | --- | --- |
| `core/buffer.py::DurableBuffer._run_scanner_sweep` (both backlog queries) | raw_payload + normalized_text -> reconstruct source/event/sender/control -> `_MessageRef.message_text` -> pipeline worker | None in scanner (readers unchanged). New rows are scrubbed at every inbound writer; GAP S remains for historical alternate-writer content, and the queue is not a sanitization boundary. | `tests/core/test_buffer.py` real stored-row recovery + PG restart/claim tests; G2. |
| `modules/pipeline.py::_load_realtime_history` (time and count windows) | normalized_text `AS raw_content`, raw metadata -> history/prompt/context, includes outbound direction | None in loader (reader unchanged). New rows are scrubbed at write; GAP E remains for historical alternate-writer rows: a synthetic pre-fix row returns its code. Recovery exclusion/union/dedup preserve content. | `tests/modules/test_module_pipeline.py` history -> actual prompt; outbound contract gate; G2. |
| `modules/pipeline.py::_load_email_history` | thread normalized_text + raw metadata -> email classification/session history | None (reader unchanged). GAP S only for historical contaminated rows; new rows are scrubbed at write. | Real email-history/legacy reader tests; G2. |
| `MessagePipeline._load_decomp_conversation_messages` | SELECT raw_payload -> copied `payload.raw.conversation_history` message dictionaries, including `text` -> authoritative excerpts/decomposition input | None. GAP S for contaminated stored conversation text; no normalized_text projection in this query. | Actual loader -> normalizer/UPDATE -> reader/API fixture plus PG readback; G2 `bu-ksao0y`. |
| `MessagePipeline._load_dashboard_context` | SELECT raw_payload -> `payload.raw` conversation_id/message_id/page_context -> routing context | No normalized_text/body returned. SAFE-SCOPE S for named IDs; UNKNOWN for unrestricted page_context text/descendant content. No blanket metadata-only verdict. | Actual dashboard page_context caller/sink and field contract; G2. |
| `MessagePipeline.process` wellness/policy raw-payload lookup | accepted raw_payload -> wellness direct tool arguments (`new_state`, attributes), bypass prompt/route | No independent text scrub; typed measurements narrower than arbitrary attributes. UNKNOWN exact field contract; GAP S for generic raw content trust. | HA wellness/pipeline bypass fixture, actual role/readback; G2/G4. The separate decomposition lifecycle writer is inventoried above. |
| `core/ingestion_events.py::ingestion_event_get_inbox_lifecycle` (791; SELECT 817); `api/routers/ingestion_events.py::get_ingestion_event` (670) | SELECT lifecycle_state **and decomposition_output**, object or legacy JSON string -> unrestricted excerpt/tool_args object -> actual detail API with `include=decomposition` | No independent scrub. GAP S/E for contaminated synthetic/legacy stored decomposition disclosure; actual route default omits the field (SAFE-SCOPE E for that default projection). Opt-in `decomposition_disclosed` access audit is not redaction. | G2 `bu-ksao0y`: actual normalizer/writer/reader/route executes with fake effects; full process/runtime/provider, real PG/codec/roles/concurrent/ack outcomes UNKNOWN. |
| `core/ingestion_events.py::ingestion_event_get_payload`; `api/routers/ingestion_events.py` raw route | raw_payload (object/string; top-level content else pretty full JSON) -> returned `content`, byte count/truncation -> dashboard raw view; route emits access audit | No content scrub; access audit/32-KiB truncation is not redaction. GAP E payload function. HTTP route/authorization evidence UNKNOWN in this audit. | Real route response and authenticated server-derived audit fixture; G2. |
| `jobs/calendar_prep.py::_fetch_message_context_by_entity` | latest normalized_text + request-context subject -> attendee thread subject/snippet -> precomputed prep context | `_truncate_snippet` only bounds length; no scrub. GAP S for legacy body/subject. Exception is fail-open empty context. | `tests/jobs/test_calendar_prep_message_context.py` stored fixture -> actual prep sink; G2. |
| `roster/relationship/api/router.py::list_entity_message_threads` | latest normalized_text `AS last_snippet` -> `MessageThreadSummary` -> entity-tab API | None; limit/row ranking do not redact. GAP S. | `roster/relationship/tests/test_entity_tabs.py` real API response; G2. |
| `roster/switchboard/tools/routing/correct_route.py::correct_route` | raw_payload + normalized_text + context + attachments -> re-dispatch prompt/context | No independent content scrub; re-dispatch receives original row text/raw context. GAP S. Retention check/target routing is not redaction. | `roster/switchboard/tests/test_correct_route.py` actual dispatch capture + PG; G2. |
| `roster/switchboard/tools/routing/rule_promotion.py::_fetch_evidence_headers` | payload.raw.headers -> stringified header maps -> deterministic rule suggestion evidence | No scrub at reader; body excluded, but headers can include subject. UNKNOWN field/header scope and downstream proposed-condition population; do not call all headers safe. | Stored headers -> promotion output/persisted suggestion fixture; G2/G4. |
| `core/fact_authority.py::FactSourceContextRegistry.capture_accepted_report`, `verify`, `recover` | raw_payload + normalized_text + request_context -> `accepted_source_digest`; returned report/claim/digest, not message text | SAFE-SCOPE S: named return projection discloses source report/digest, not raw content. Bytes are still consumed in memory; this is no sanitizer. | Real synthetic digest/admission recovery tests; PG/catalog UNKNOWN. Never rewrite hash inputs under a text-display fix without authority. G2 compatibility gate. |
| `scripts/backfill_batch_sender_identities.py` main query/caller | raw_payload participants/sender -> update identity/provenance context | SAFE-SCOPE S: identity-only result; script is a maintenance reader, not a display/prompt sanitizer. | Synthetic script fixture/role readback; no live script run. G2 preservation gate. |
| `core_tools/_switchboard.py::register_switchboard_core_tools` ingest/session wrapper | Caller envelope text -> central ingest then durable enqueue/pipeline/session; indirect inbox consumer, not new SQL owner | SAFE-SCOPE S/E: `scrub_message_text` before normal enqueue; it does not cover scanner/legacy readers or attached raw context. | Current central helper/normal connector tests pass; actual enqueue/session legacy path remains G2. |
| `dead_letter/capture.py`, `dead_letter/replay.py`; `api/routers/approvals.py::_unroutable_question` | Capture supplied original content; replay reinsert; approval question extracts dead-letter content. These are downstream/alternate stores, not an additional direct inbox text SELECT. | Capture and replay now scrub `original_payload` before their INSERT (`bu-ksao0y`), so the approval question reads scrubbed text for new dead letters. Dead letters captured before the fix hold raw text until replayed, and the `_unroutable_question` reader is unchanged (historical rows remain a gap). API source-table filters are metadata-only. | Real capture/approval response/replay tests; G2. |

The following direct readers are **SAFE-SCOPE S for their exact no-text projection**;
this is not an access-control or deployed-schema verdict. They must remain in the inventory:

| Owner | Exact projection / use |
| --- | --- |
| `core/ingestion_events.py` reconciliation and replay/reset helpers | lifecycle/status/IDs and counts; exception strings are not certified by this no-text verdict. `ingestion_event_get_inbox_lifecycle` unrestricted decomposition reader is separately GAP above. |
| `MessagePipeline._accept_ingress` dedupe SELECT; `process` accepted->processing lifecycle UPDATE | request ID/state only; the alternate INSERT is now scrubbed for new rows (`bu-ksao0y`); the `_update_message_inbox_lifecycle` text-descendant UPDATE is separately GAP |
| `ingest.py` dedupe/content-hash lookups and partition ensure | ID/request context or DDL; the hash keys derive from scrubbed text, and only `content_hash_key` is omitted from `request_context` when bearer material was withheld; `dedupe_key` remains (`bu-y0cll7`; rows stored before it keep raw-text hashes until `bu-tnm8zn`); central content INSERT separately scrubbed |
| `roster/relationship/jobs/relationship_jobs.py::run_interaction_sync`, `run_email_identity_enrichment` | sender/thread/time/identity aggregates; no normalized_text or body projection |
| `roster/switchboard/api/router.py::get_ingestion_overview` | counts/lifecycle aggregates |
| `roster/switchboard/modules/tools.py::correct_route` wrapper | request metadata before delegating to the text-reading correction implementation |
| `roster/switchboard/tools/operator/controls.py::manual_reroute`, `cancel_request`, `abort_session`, `force_complete` read projections | routing/lifecycle/request metadata; manual_reroute selects dispatch_outcomes but consumes only target, not whole content. Operator summary writes are separately UNKNOWN above. |
| `api/routers/approvals.py::list_unroutable_attention`, `retry_unroutable_attention` | attention/source-table/replay metadata; dead-letter content question separately listed |

Other references have an explicit exclusion: ingestion-event/relationship models describe
responses; `contacts/email_identity_matching.py` documents that it does not read the inbox;
`chronicler/adapters/comms.py` explicitly uses messaging memory instead of inbox text;
core read-grant/conversation-anchor migrations and Switchboard partition/index/identity
migrations are DDL or identity transforms. `040` is the content maintenance reader already
listed. Tests are test harnesses, not deployed readers. There is no additional checked-in
text view/export query at this source SHA; an unresolved dynamic/deployed query remains
UNKNOWN, not an assumed exclusion.

## Executing evidence and remaining axes

The self-contained probe below ran in this worktree's **real, nonsymlinked venv** against
the stated source. All **25 checks passed** (original 18 plus seven descendant checks).
Positive checks deliberately confirm that
the fabricated public code survives a real function's bind, return, prompt or log.
Such a PASS confirms a **GAP**, not safe custody. The mock objects only replace DB,
transport and inference effects; no real store write, network submission or paid model ran.

The F1 correction separately executes source-excerpt/tool-argument normalization,
lifecycle UPDATE binds (including response_summary), object/string lifecycle reads and
the actual opted-in detail route; it also verifies default omission. That is bounded
writer/reader/route evidence, not execution of the full decomposition process, HTTP
authentication/audit persistence, runtime/provider or PostgreSQL. The
[conversation-decomposition excerpt and storage contract](../../openspec/specs/conversation-decomposition/spec.md)
requires source text excerpts and full result storage. G2 `bu-ksao0y` must reconcile this
contract before product changes; this documentation correction grants no repair authority.

Existing behavior tests also passed:

```bash
uv run --no-sync pytest tests/test_ingestion_bearer_scrub.py::test_stored_record_aggressive_mode_only_rewrites_message_content tests/connectors/test_gmail_connector.py::test_build_ingest_envelope_withholds_bearer_material tests/connectors/test_telegram_user_client.py::test_login_code_from_service_sender_is_withheld -q --tb=short -n 0
# 3 passed in 2.67s
uv run --no-sync pytest tests/test_ingestion_bearer_scrub.py tests/connectors/test_filtered_event_buffer.py tests/connectors/test_gmail_connector.py tests/connectors/test_telegram_user_client.py -q --tb=short
# 169 passed in 4.40s
go -C whatsapp-bridge test ./internal/api -run '^TestServer_Backfill_(RepeatedAcceptedRequestsKeepPendingReplayUniqueAndBounded|BoundsAndDeduplicatesLiveHandoffBeforeFirstSubscription|QueuesLivePublishBetweenSnapshotAndHandoffExactlyOnce|ReplaysBufferedMessagesViaSSE|HandoffsLiveEventBeforeFirstSSESubscription)$' -count=1 -timeout=300s
# PASS: five selected tests; package receipt 0.016s
```

The F1 documentation correction added no product or test code. Its new scoped receipts:

```bash
uv run --no-sync pytest tests/api/test_ingestion_events.py::test_decomposition_output_omitted_by_default tests/api/test_ingestion_events.py::test_decomposition_output_included_with_flag tests/modules/test_module_pipeline.py::test_decomposition_loader_preserves_structured_speaker_messages -q --tb=short -n 0
# 3 passed in 1.58s
uv run --no-sync pytest tests/api/test_ingestion_events.py -q --tb=short
# 69 passed in 6.78s
```

These named scopes verify detector content/metadata behavior, normal connector scrubs,
existing buffer semantics, native replay bounds and the API's decomposition include/default
behavior. They do not cover every connector's
error branch, all reader sinks, deployment, durable persistence or every custody fix.
Tests: `+0 ~0 -0`; this audit adds no test inventory.

Docker access returned permission denied on `/var/run/docker.sock`. Therefore real
PostgreSQL/production JSONB codec, independent post-transaction readback, actual runtime
roles/RLS/catalog, concurrent replay/flush/checkpoint and commit-success/ack-failure
outcomes are **UNKNOWN**. The target is a disposable schema migrated with the actual
bootstrap, actual `SET ROLE` producers/readers and separate-connection readback of
synthetic fixtures, followed by restart/concurrent fault tests. No alternate credentials,
privilege escalation or live database were used to fill this gap.

| Axis | Evidence now / precise exit evidence |
| --- | --- |
| Bearer positives, ordinary-number negatives, aggressive 777000, nested/encoded content, placeholders, identity preservation, idempotence | Existing detector/connector tests execute these within their named scopes. Full fleet producer/error/reader matrix still needs each correction's actual callsite tests. |
| Preview failure | Probe substitutes preview failure at actual record seam: null preview but full payload survives. Full detector exception before every durable write remains UNKNOWN; require adopted metadata-only/failure policy and real writer tests. |
| Legacy strings/objects and reinsert | Actual filtered replay and dead-letter replay functions execute synthetic legacy cases; top-level legacy content survivor shown. Actual PG codec/transaction effect UNKNOWN. |
| Decomposition text descendants | Actual normalizer -> lifecycle UPDATE bind -> object/string lifecycle reader -> opted-in detail route retains fabricated source text/tool_args; default route omits decomposition. Summary bind also retains submitted text. Full process/provider and durable/role axes UNKNOWN; G2. |
| Failure/concurrency/restart | Source locks, row eligibility, flush-drop behavior and native handoff tests traced. Real concurrent PG lock/claim/cursor/reset outcomes UNKNOWN; mock call order is not durability proof. |
| Commit succeeded, acknowledgement failed | UNKNOWN: independently committed fixture, injected post-commit transport failure, restart/replay and duplicate readback required at each owned producer. |
| Historical records / rollback | No real historical read or cleanup. Audit docs are reversible; a later code revert cannot restore withheld historical values. Any live backfill/purge is separately owner-authorized and not part of this packet. |
| Binary/audio/vendor/native library custody | UNKNOWN unless exact contract/harness closes that store; text detection cannot certify bytes. Auth/session keys remain under credential contracts. |

## Finding dispositions and original completion limits

The coordinator filed these four bounded issues after deduplication. Each is blocked
with conditional `spec-first` / `needs-shaping` gates and a `discovered-from` relationship
to `.22`, avoiding a fix-or-file closure cycle. Filing is a durable disposition, not
authorization to repair. The coordinator retains ownership of lifecycle and dependencies:

| Packet | Owned outcome / adoption gate | Exit evidence and source owner |
| --- | --- | --- |
| G1, `bu-v3pw5l`: filtered full-payload/error/replay custody | Fixed in `bu-v3pw5l`: no separate contract adoption was needed, because the persisted-payload requirement and `scrub_envelope`'s replay-queue contract already decide the behavior; the gate was dropped. `bu-ly3lv5.14` (shared kit) is not waited on; the kit move can carry the change. Event/source/sender/control, decisions, eligibility/status and compatible replay are preserved. No live history cleanup. | Actual caller -> F write/readback -> locked replay/submit; nested/encoded/legacy/failure/negative/idempotence/concurrent/restart/ack tests. Shared connector/evaluator maintainers. |
| G2, `bu-ksao0y`: legacy inbox writers and all text readers | Close alternate inbound writer shapes and legacy/reader projections under owning API/session/ingest contracts, explicitly including decomposition excerpt/tool_args -> lifecycle UPDATE -> object/string lifecycle reader -> opted-in detail API, plus written response_summary. Reconcile source-excerpt/storage fidelity, outbound exclusions and accepted-source digest compatibility before changes; no authority/auth change or implicit historical mutation. | Every writer/reader matrix row has executing sink proof; actual PG codec/roles/readback, full decomposition process/runtime, summary population, scanner recovery, API responses, histories/prep/correction/approval prompts and metadata/digest non-disclosure. Bounded synthetic F1 tests do not earn these UNKNOWN axes. Switchboard/runtime/API/relationship owners. |
| G3, `bu-rob5w5`: pre-ingest discretion and producer logs | Apply the adopted detector before governed text enters context/prompt/log egress; preserve weight/channel/group/filter semantics and source identity. No actual inference in verification; provider retention remains separate. | Actual connector -> evaluator/dispatcher fake transport plus log capture, history/bypass/error/negative/idempotence cases; synthetic runtime/provider logging harness. Connector/runtime owners. |
| G4, `bu-hxyqq9` (partly fixed: OwnTracks `raw_payload` strings, Drive cached names and ActivityWatch `window_title` are scrubbed or skipped on detector failure; Home Assistant person history was already scrubbed by #4550; ActivityWatch `raw_payload` URL/tab title is still pending, and its scrub changes the existing test that pins the raw tokenised URL): evidence/cache and auxiliary-store contracts | Reconcile ActivityWatch title/raw, Drive names/settings, OwnTracks raw evidence, HA person attributes, Spotify/Steam metadata and native/binary/audio custody against exact specs and fidelity/retention. No credentials, live stores or purges. | Closed field population and disposable DB/vendor harness; each gap has adopted correction or durable issue; each absent normative contract has owner choices. Connector/evidence owners. |

G4 owner choices where existing contracts cannot decide: **(A, recommended after
field/spec adoption)** sanitize the governed message-bearing fields before durable
storage while preserving identity/numeric evidence/replay; **(B)** adopt metadata-only
evidence for explicit fields and acknowledge the evidence-loss tradeoff; **(C, status quo
default)** leave product unchanged and retain UNKNOWN/risk findings until authority and
harness are owned. None of these choices authorizes credential or binary deletion.
The conflicts are concrete: [ActivityWatch's forensic evidence requirement](../../openspec/specs/connector-activitywatch/spec.md)
retains raw title and correlated URL/tab title while excluding them from normal
envelopes; [OwnTracks Durable Location Evidence Fidelity](../../openspec/specs/connector-owntracks/spec.md#requirement-durable-location-evidence-fidelity)
forbids deleting, renaming or normalizing accepted optional evidence fields; and
[Drive's metadata-cache requirement](../../openspec/specs/connector-google-drive/spec.md)
persists names. Selecting A or B requires explicit compatible amendments to those
field contracts where necessary. The audit does not settle precedence by silently
rewriting evidence under the broad base requirement.
G2's outbound/source-digest compatibility needs exact adoption before implementation; G1's
replay-data contract needed none (decided in `bu-v3pw5l`). Existing preview work and `.21` general docs do
not dispose these gaps; `.19` metrics and `.20` typed observation policy are separate.

Original acceptance remains: (1) every connector queue and inbox reader has a verdict,
(2) fixes or filed beads for **each** gap, (3) tests at the real seam. This PR supplies the
source matrices and bounded executing evidence; the coordinator's four issues satisfy
the filed-gap disposition path. The worker performed no product fixes or issue mutations.
The durable/role axes and enumerated missing caller/sink harnesses remain UNKNOWN with
precise targets in those issues; the audit report must land. The coordinator must retain
the original acceptance limits when reconciling closure. Inventory alone is not
original-bead completion or fleet custody certification.

## Self-contained synthetic reproducer

At the stated source SHA, create an owned venv with `uv sync --dev` if one is absent
(never sync a symlinked venv). Extract this block to a temporary file outside the repo
and run it from the worktree. It prints check names only, never fixture-bearing log or
payload values. The deliberately public fixture below is not a credential. The check names
that say a writer retains text (for example `legacy_pipeline_insert_bind_retains_text`) record
behavior at the stated source SHA; `bu-ksao0y` later scrubbed those writers for new rows, so
re-running the block on a newer SHA is expected to flip them.

```bash
python3 - <<'PY'
from pathlib import Path
text = Path('docs/connectors/bearer-material-custody-audit.md').read_text()
probe = text.split('<!-- custody-probe:start -->\n```python\n', 1)[1].split('\n```\n<!-- custody-probe:end -->', 1)[0]
Path('/tmp/butlers-custody-audit-probe.py').write_text(probe + '\n')
PY
uv run --no-sync python /tmp/butlers-custody-audit-probe.py
```

<!-- custody-probe:start -->
```python
"""Synthetic custody probe. No network, real database, credential or message reads."""
import asyncio
import importlib.util
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from starlette.requests import Request

from butlers.api.routers import ingestion_events as ingestion_routes
from butlers.connectors.activitywatch import persist_activity_event
from butlers.connectors.discretion import DiscretionEvaluator
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.core import ingestion_events
from butlers.ingestion_bearer_scrub import scrub_envelope, scrub_stored_record
from butlers.modules.pipeline import MessagePipeline, _load_realtime_history, _normalize_decomp_signals

# This deliberately public fixture is not a credential.
TEXT = "Login code: 55123. Do not share."
TOKEN = "55123"
NOW = datetime(2026, 10, 14, tzinfo=UTC)
results = {}


def checked(name, condition):
    if not condition:
        raise AssertionError(name)
    results[name] = "PASS"


def fake_pool():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="OK")
    conn.executemany = AsyncMock(return_value=None)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value = txn
    acquisition = MagicMock()
    acquisition.__aenter__ = AsyncMock(return_value=conn)
    acquisition.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquisition
    pool.execute = AsyncMock(return_value="OK")
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=1)
    return pool, conn


async def main():
    envelope = {"source": {"channel": "telegram_user_client", "provider": "telegram",
                            "endpoint_identity": "fixture"},
                "sender": {"identity": "777000"}, "event": {"external_event_id": "fixture"},
                "payload": {"raw": {"text": TEXT}, "normalized_text": TEXT}, "control": {}}
    scrubbed, _ = scrub_envelope(envelope)
    checked("normal_envelope_scrubs_content", TOKEN not in json.dumps(scrubbed))
    checked("normal_scrub_is_idempotent", scrub_envelope(scrubbed)[0] == scrubbed)
    legacy, text, _ = scrub_stored_record({"content": TEXT}, TEXT)
    checked("legacy_top_level_content_survives_stored_record_helper",
            TOKEN in legacy["content"] and TOKEN not in text)

    full = FilteredEventBuffer.full_payload(
        channel="telegram_user_client", provider="telegram", endpoint_identity="fixture",
        external_event_id="fixture", external_thread_id="fixture", observed_at=NOW.isoformat(),
        sender_identity="777000", raw={"text": TEXT}, normalized_text=TEXT)
    buf = FilteredEventBuffer("telegram_user_client", "fixture")
    buf.record(external_message_id="fixture", source_channel="telegram_user_client",
               sender_identity="777000", subject_or_preview=TEXT, filter_reason="fixture",
               full_payload=full, error_detail=TEXT, received_at=NOW)
    pool, conn = fake_pool()
    with patch("butlers.fleet_events.publish_fleet_event", new=AsyncMock()):
        await buf.flush(pool)
    row = conn.executemany.await_args.args[1][0]
    checked("filtered_preview_is_scrubbed", TOKEN not in row[6])
    checked("filtered_insert_bind_retains_full_payload", TOKEN in json.dumps(row[9]))
    checked("filtered_insert_bind_retains_error_detail", TOKEN in row[10])
    checked("filtered_flush_consumes_buffer", len(buf) == 0)
    failing = FilteredEventBuffer("telegram_user_client", "fixture")
    with patch("butlers.connectors.filtered_event_buffer.scrub_filtered_preview", return_value=None):
        failing.record(external_message_id="fixture", source_channel="telegram_user_client",
                       sender_identity="777000", subject_or_preview=TEXT, filter_reason="fixture",
                       full_payload=full, received_at=NOW)
    checked("preview_failure_does_not_scrub_full_payload",
            failing._rows[0][6] is None and TOKEN in json.dumps(failing._rows[0][9]))

    for shape in ("object", "legacy_string"):
        replay_pool, replay_conn = fake_pool()
        replay_conn.fetch.return_value = [{"id": uuid4(), "received_at": NOW,
            "external_message_id": "fixture", "full_payload": full if shape == "object" else json.dumps(full)}]
        submit = AsyncMock()
        await drain_replay_pending(replay_pool, "telegram_user_client", "fixture", submit)
        checked("filtered_replay_" + shape + "_retains_text",
                submit.await_count == 1 and TOKEN in json.dumps(submit.await_args.args[0]))

    inbox_pool, inbox_conn = fake_pool()
    inbox_conn.fetchrow.side_effect = [None, {"request_id": uuid4()}]
    pipeline = MessagePipeline(inbox_pool, AsyncMock(), enable_ingress_dedupe=True)
    await pipeline._accept_ingress(message_text=TEXT, args={"sender_id": "fixture", "chat_id": "fixture"},
        source_metadata={"source_channel": "telegram_bot", "source_endpoint_identity": "fixture"},
        source="telegram_bot", chat_id="fixture")
    insert = next(c.args for c in inbox_conn.fetchrow.await_args_list if "INSERT INTO message_inbox" in c.args[0])
    checked("legacy_pipeline_insert_bind_retains_text", TOKEN in json.dumps(insert[3]) and TOKEN in insert[4])

    inbox_pool.fetchrow.return_value = {"raw_payload": {"content": TEXT}, "source_channel": "telegram_bot"}
    with patch.object(ingestion_events, "ingestion_event_get", new=AsyncMock(return_value={"source_channel": "telegram_bot"})):
        payload = await ingestion_events.ingestion_event_get_payload(inbox_pool, inbox_pool, uuid4())
    checked("payload_reader_returns_legacy_text", TOKEN in payload["content"])

    history_pool, history_conn = fake_pool()
    history_conn.fetch.return_value = [{"raw_content": TEXT, "sender_id": "fixture", "received_at": NOW,
                                        "raw_metadata": {}, "direction": "inbound"}]
    history = await _load_realtime_history(history_pool, "fixture", NOW, source_channel="telegram_bot")
    checked("realtime_history_returns_stored_text", TOKEN in history[0]["raw_content"])

    dispatcher = MagicMock(spec=["call"])
    dispatcher.call = AsyncMock(return_value='{"verdict":"FORWARD","reason":"fixture"}')
    captured_logs = []
    class Capture(logging.Handler):
        def emit(self, record):
            captured_logs.append(record.getMessage())
    discretion_logger = logging.getLogger("butlers.connectors.discretion")
    old_level, old_propagate = discretion_logger.level, discretion_logger.propagate
    handler = Capture()
    discretion_logger.addHandler(handler)
    discretion_logger.setLevel(logging.INFO)
    discretion_logger.propagate = False
    try:
        evaluator = DiscretionEvaluator("fixture", dispatcher)
        await evaluator.evaluate(TEXT, weight=0.5)
        checked("discretion_prompt_retains_text", TOKEN in dispatcher.call.await_args.args[0])
        checked("discretion_window_retains_text", TOKEN in evaluator.window.entries[0].text)
        checked("discretion_log_retains_text", any(TOKEN in line for line in captured_logs))
    finally:
        discretion_logger.removeHandler(handler)
        discretion_logger.setLevel(old_level)
        discretion_logger.propagate = old_propagate

    evidence_pool, _ = fake_pool()
    await persist_activity_event(evidence_pool, machine_id="fixture", endpoint_identity="fixture", bucket_id="fixture",
        ts=NOW, duration_seconds=1, app="fixture", window_title=TEXT, app_class="other", browser_domain=None,
        is_afk=False, raw_payload={"title": TEXT})
    evidence = evidence_pool.fetchval.await_args.args
    checked("activity_evidence_bind_retains_title_and_raw", TOKEN in evidence[8] and TOKEN in json.dumps(evidence[12]))

    path = Path("roster/switchboard/tools/dead_letter/replay.py")
    spec = importlib.util.spec_from_file_location("audit_dead_letter_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, dead_conn = fake_pool()
    dead_id = uuid4()
    dead_conn.fetchrow.return_value = {"id": dead_id, "original_request_id": uuid4(),
        "request_context": {}, "original_payload": {"content": TEXT}, "replay_eligible": True, "replayed_at": None}
    await module.replay_dead_letter_request(dead_conn, dead_letter_id=dead_id,
                                           operator_identity="synthetic-fixture", reason="synthetic-fixture")
    dead_insert = next(c.args for c in dead_conn.execute.await_args_list if "INSERT INTO switchboard.message_inbox" in c.args[0])
    checked("dead_letter_replay_insert_bind_retains_text", TOKEN in dead_insert[3] and TOKEN in dead_insert[4])

    # Decomposition stores message-content descendants, not just lifecycle metadata.
    signal = _normalize_decomp_signals(
        [{"target_butler": "general", "signal_type": "report", "tool_args": {"message": TEXT},
          "excerpts": [{"message_id": "fixture"}]}],
        authoritative_by_message_id={"fixture": {"message_id": "fixture", "text": TEXT,
            "sender_identity": "fixture", "sender": "Fixture", "timestamp": NOW.isoformat()}},
    )[0]
    checked("normalized_signal_retains_source_excerpt_and_tool_args",
            TOKEN in signal["excerpts"][0]["text"] and TOKEN in signal["tool_args"]["message"])
    descendant_pool, descendant_conn = fake_pool()
    event_id = uuid4()
    descendant_pipeline = MessagePipeline(descendant_pool, AsyncMock())
    await descendant_pipeline._update_message_inbox_lifecycle(
        message_inbox_id=event_id, decomposition_output={"signals": [signal]},
        dispatch_outcomes={"target": "general"}, response_summary=TEXT, lifecycle_state="routed",
        classified_at=NOW, classification_duration_ms=1, final_state_at=NOW,
    )
    bound = descendant_conn.execute.await_args.args
    checked("lifecycle_writer_update_bind_retains_text", TOKEN in json.dumps(bound[1]))
    checked("lifecycle_writer_response_summary_bind_retains_text", TOKEN in bound[3])
    for shape in ("object", "legacy_string"):
        descendant_pool.fetchrow.return_value = {"lifecycle_state": "routed",
            "decomposition_output": bound[1] if shape == "object" else json.dumps(bound[1])}
        returned = await ingestion_events.ingestion_event_get_inbox_lifecycle(descendant_pool, event_id)
        checked("lifecycle_reader_" + shape + "_returns_text", TOKEN in json.dumps(returned))

    db = MagicMock()
    db.pool.return_value = descendant_pool
    db.credential_shared_pool.return_value = MagicMock()
    request = Request({"type": "http", "method": "GET", "path": "/synthetic", "headers": []})

    async def synthetic_event(*_args):
        return {"id": str(event_id), "received_at": NOW}

    with patch.object(ingestion_routes, "ingestion_event_get", new=synthetic_event), \
         patch.object(ingestion_routes, "emit_dashboard_audit", new=AsyncMock()), \
         patch.object(ingestion_routes, "ingestion_events_replay_policy", new=AsyncMock(return_value={})):
        response = await ingestion_routes.get_ingestion_event(
            str(event_id), request, include=["decomposition"], db=db)
        checked("actual_detail_route_include_decomposition_returns_text", TOKEN in response.model_dump_json())
        response = await ingestion_routes.get_ingestion_event(str(event_id), request, include=[], db=db)
        checked("actual_detail_route_default_omits_decomposition", response.data.decomposition_output is None)

    print(json.dumps({"scope": "synthetic real-function execution; fake DB/transport only", "checks": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.CRITICAL)
    asyncio.run(main())
```
<!-- custody-probe:end -->
