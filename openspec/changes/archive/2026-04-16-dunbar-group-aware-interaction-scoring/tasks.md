## 1. Envelope Model Extensions

- [ ] 1.1 Add `participant_count: int | None = None` and `chat_type: str | None = None` to `IngestSenderV1` in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `interaction_eligible: bool = True` to `IngestControlV1` in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Propagate `participant_count`, `chat_type`, and `interaction_eligible` in `_build_request_context()` in `roster/switchboard/tools/ingestion/ingest.py`
- [ ] 1.4 Tests: verify envelope validation accepts new optional fields and request_context propagation

## 2. Dunbar Scoring Formula

- [ ] 2.1 Add direction weight constants (`DIRECTION_WEIGHT_OUTGOING=10.0`, `DIRECTION_WEIGHT_MUTUAL=5.0`, `DIRECTION_WEIGHT_INCOMING=1.0`) to `roster/relationship/tools/dunbar.py`
- [ ] 2.2 Update `compute_dunbar_scores()` SQL to apply direction weight via `CASE metadata->>'direction'` and group_size divisor via `1.0 / GREATEST(COALESCE((metadata->>'group_size')::float, 1.0), 1.0)`
- [ ] 2.3 Tests: verify scoring with direction=outgoing produces 10x score vs direction=incoming; verify group_size=10 produces 1/10th score; verify NULL defaults to 1.0x for both

## 3. Connector-Level Participant Gating

- [ ] 3.1 Add `max_interaction_group_size` config field to Telegram user client connector config (default: 20)
- [ ] 3.2 Query `chat.participants_count` in `_flush_chat_buffer()` and `_process_message()`, cache per chat_id with 1-hour TTL
- [ ] 3.3 Include `participant_count` and `chat_type` in envelope `sender` section
- [ ] 3.4 Set `control.interaction_eligible = false` when `participant_count > max_interaction_group_size`
- [ ] 3.5 Add OTel counter `butlers.telegram_user_client.interaction_gated`
- [ ] 3.6 Apply equivalent enrichment to WhatsApp user client connector (`src/butlers/connectors/whatsapp_user_client.py`) using bridge metadata for participant count
- [ ] 3.7 Tests: verify envelope enrichment for DM (participant_count=2, chat_type=private), small group, and large group (interaction_eligible=false)

## 4. Interaction Sync Group-Aware Pre-Grouping

- [ ] 4.1 Rewrite `run_interaction_sync()` SQL query to GROUP BY `(source_thread_identity, source_channel, DATE(received_at))` with DISTINCT sender aggregation
- [ ] 4.2 Add `interaction_eligible` filter: skip messages where `request_context->>'interaction_eligible' = 'false'`
- [ ] 4.3 Add participant count gate: skip chat groups with >20 distinct senders (or request_context participant_count >20)
- [ ] 4.4 Implement direction detection: partition senders into owner/non-owner, set direction based on owner presence in chat
- [ ] 4.5 Add outgoing hour offsets: extend `_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET` with +12 offsets for outgoing facts
- [ ] 4.6 Inject `group_size` into interaction fact metadata for group chats
- [ ] 4.7 Tests: verify group pre-grouping, direction detection, >20 cutoff, group_size in metadata, outgoing deduplication

## 5. Batch Group Interaction Tool

- [ ] 5.1 Implement `interaction_log_group()` in `roster/relationship/tools/interactions.py`
- [ ] 5.2 Register `interaction_log_group` as MCP tool in `roster/relationship/modules/tools.py`
- [ ] 5.3 Tests: verify fan-out for group with 5 members, empty group returns zeros, >20 members returns group_too_large, group_size in all created facts

## 6. Integration Testing

- [ ] 6.1 End-to-end test: Telegram message in 5-person group → connector enrichment → Switchboard propagation → interaction_sync creates facts with group_size=5 and correct directions → Dunbar score reflects 1/5 weight and direction multiplier
- [ ] 6.2 End-to-end test: Telegram message in 50-person group → connector sets interaction_eligible=false → interaction_sync skips → no Dunbar score impact
