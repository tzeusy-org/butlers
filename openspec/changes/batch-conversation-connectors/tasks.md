## 1. Connector Config Defaults & Envelope Tagging

- [ ] 1.1 Update `TelegramUserClientConnectorConfig`: change `flush_interval_s` default from 600 to 1800, `history_time_window_m` default from 30 to 35
- [ ] 1.2 Update `WhatsAppUserClientConnectorConfig`: change `flush_interval_s` default from 600 to 1800, `history_time_window_m` default from 30 to 35 (mirror telegram changes)
- [ ] 1.3 Add `control.payload_type = "conversation_history"` to `_build_batch_envelope()` in `telegram_user_client.py`
- [ ] 1.4 Add `control.payload_type = "conversation_history"` to batch envelope assembly in `whatsapp_user_client.py`
- [ ] 1.5 Update tests for both connectors: verify new defaults and payload_type tag in envelope output

## 2. Dashboard Settings Live Reload

- [ ] 2.1 Add settings read in `_flush_scanner_loop()` for telegram_user_client: read `flush_interval_s` from `connector_registry.settings` JSONB, fallback to env var, then default
- [ ] 2.2 Add settings read in `_flush_scanner_loop()` for whatsapp_user_client: same precedence chain (dashboard > env > default)
- [ ] 2.3 Add unit tests for settings precedence: dashboard override > env var > hardcoded default

## 3. Dashboard Batch Settings Card

- [ ] 3.1 Create `BatchSettingsCard` React component: editable `flush_interval_s` input with validation (min 60, max 7200), "custom" vs "default" label, no restart notice
- [ ] 3.2 Integrate `BatchSettingsCard` into `ConnectorDetailPage.tsx`: render only for `telegram_user_client` and `whatsapp_user_client` connector types
- [ ] 3.3 Wire save action to existing `useUpdateConnectorSettings()` mutation with `{"settings": {"flush_interval_s": value}}`

## 4. Pipeline Decomposition Branch

- [ ] 4.1 Add `payload_type` detection in `pipeline.process()`: check `raw_payload.control.payload_type == "conversation_history"` after triage decision handling
- [ ] 4.2 Implement `_decompose_conversation()` method: load signal-extraction prompt template + butler schemas from skill directory, invoke LLM API directly, parse JSON array response
- [ ] 4.3 Implement cherry-pick excerpt assembly: for each signal in extraction result, build conceptual message with `{signal_type, target_butler, tool_name, tool_args, excerpts, confidence}` by selecting relevant messages from conversation_history
- [ ] 4.4 Implement fan-out routing: iterate conceptual messages, call `route()` per target butler, accumulate `dispatch_outcomes`
- [ ] 4.5 Implement empty decomposition handling: when extraction returns `[]`, set `decomposition_output` to `{"signals": [], "reason": "no_signals_extracted"}`, set `lifecycle_state` to `"decomposed_empty"`, emit `butlers.pipeline.decomposition_empty` metric
- [ ] 4.6 Store decomposition results: write full extraction result + metadata (model, latency_ms, token_usage) to `decomposition_output` JSONB field on `message_inbox`

## 5. Testing & Integration

- [ ] 5.1 Unit tests for `_decompose_conversation()`: mock LLM API, verify JSON array parsing, verify cherry-pick excerpt assembly, verify empty array handling
- [ ] 5.2 Unit tests for decomposition branch in `pipeline.process()`: verify payload_type detection, verify policy bypass still honored, verify skip/metadata_only still honored
- [ ] 5.3 Unit tests for fan-out routing: verify multiple `route()` calls, verify `dispatch_outcomes` recording, verify partial failure handling
- [ ] 5.4 Integration test: end-to-end flow from connector flush with `payload_type` through decomposition to multi-butler routing
- [ ] 5.5 Update existing pipeline tests: ensure standard (non-conversation_history) messages are unaffected by the new decomposition branch
