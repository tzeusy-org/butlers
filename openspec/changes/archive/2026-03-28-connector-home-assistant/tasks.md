## 1. Switchboard Routing Registration

- [ ] 1.1 Add `"home_assistant"` to `SourceChannel` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `"home_assistant"` to `SourceProvider` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Add `"home_assistant": frozenset({"home_assistant"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- [ ] 1.4 Update routing contract tests in `tests/test_routing_contracts.py` for new channel/provider pair

## 2. Dashboard Settings — HA Connection Configuration

- [ ] 2.1 Add HA settings API endpoint: `GET /api/settings/home-assistant` to retrieve current HA connection status (connected/disconnected, URL masked)
- [ ] 2.2 Add HA settings API endpoint: `POST /api/settings/home-assistant` to validate and save HA URL + access token to CredentialStore
- [ ] 2.3 Implement connection validation logic — test `GET /api/` against the provided HA URL with bearer token, return specific error messages for unreachable/auth-failure/unexpected
- [ ] 2.4 Add HA settings API endpoint: `DELETE /api/settings/home-assistant` to remove stored HA credentials from CredentialStore
- [ ] 2.5 Add Pydantic request/response models for HA settings endpoints
- [ ] 2.6 Write tests for HA settings endpoints (validation success, validation failure cases, credential storage/removal)

## 3. Connector Core — WebSocket Client

- [ ] 3.1 Create `src/butlers/connectors/home_assistant.py` with connector entrypoint and process lifecycle
- [ ] 3.2 Implement WebSocket authentication handshake (connect, auth_required, auth response, auth_ok/auth_invalid)
- [ ] 3.3 Implement event subscription for `state_changed`, `automation_triggered`, and `call_service` event types
- [ ] 3.4 Implement WebSocket ping/pong keepalive (30s ping interval, 10s pong timeout)
- [ ] 3.5 Implement exponential backoff reconnection logic (1s to 60s cap) with health state transition to `degraded`
- [ ] 3.6 Implement event message parsing and dispatch to filter pipeline

## 4. Connector Core — REST Polling Fallback

- [ ] 4.1 Implement REST polling client (`GET /api/states` with bearer token auth)
- [ ] 4.2 Implement in-memory state cache for diff-based change detection between polls
- [ ] 4.3 Implement fallback activation (after 3 consecutive WS reconnection failures) and deactivation (on WS reconnect)
- [ ] 4.4 Implement configurable poll interval via `HA_POLL_INTERVAL_S`

## 5. Three-Layer Filtering Pipeline

- [ ] 5.1 Implement Layer 1 — domain allowlist filter with configurable domain list
- [ ] 5.2 Implement Layer 2 — significance filter with per-device-class thresholds for numeric sensors
- [ ] 5.3 Implement significance filter bypass for binary entities and `unavailable`/`unknown` state transitions
- [ ] 5.4 Integrate Layer 3 — shared `DiscretionEvaluator` and `DiscretionDispatcher` with per-domain context windows
- [ ] 5.5 Implement filter pipeline metrics (`connector_ha_events_total`, `connector_ha_filter_pass_rate`)
- [ ] 5.6 Write tests for each filter layer (domain exclusion, significance thresholds, discretion integration)

## 6. ingest.v1 Envelope Construction

- [ ] 6.1 Implement `state_changed` event to `ingest.v1` envelope mapping (source, event, sender, payload, control fields)
- [ ] 6.2 Implement `automation_triggered` event to `ingest.v1` envelope mapping
- [ ] 6.3 Implement `normalized_text` generation using `friendly_name`, old/new state values, and `unit_of_measurement`
- [ ] 6.4 Implement idempotency key construction: `"ha:<endpoint_identity>:<entity_id>:<time_fired_unix_ms>"`
- [ ] 6.5 Write tests for envelope construction (field mapping, normalized text formatting, idempotency keys)

## 7. Checkpoint and Resume

- [ ] 7.1 Implement checkpoint persistence via `cursor_store` with `last_event_ts`, `last_entity_id`, and `transport` fields
- [ ] 7.2 Implement checkpoint loading on restart with safety margin subtraction (`HA_CHECKPOINT_OVERLAP_S`)
- [ ] 7.3 Implement event dedup based on checkpoint (skip events at or before adjusted checkpoint timestamp)
- [ ] 7.4 Write tests for checkpoint save/load/resume cycle

## 8. Health, Heartbeat, and Metrics

- [ ] 8.1 Implement health state derivation (healthy/degraded/error based on WS connection, REST fallback, discretion availability)
- [ ] 8.2 Implement heartbeat assembly with transport mode in `status.error_message`
- [ ] 8.3 Register HA-specific Prometheus counters (`connector_ha_events_total`, `connector_ha_ws_reconnects_total`, `connector_ha_rest_polls_total`, `connector_ha_discretion_total`)
- [ ] 8.4 Register HA-specific Prometheus gauges (`connector_ha_filter_pass_rate`, `connector_ha_transport_mode`, `connector_ha_entities_tracked`)
- [ ] 8.5 Register HA-specific Prometheus histograms (`connector_ha_event_latency_seconds`, `connector_ha_filter_pipeline_seconds`)

## 9. Filtered Event Persistence and Replay

- [ ] 9.1 Implement filtered event batch flush with HA-specific `filter_reason` values (`domain_excluded`, `insignificant_delta`, `discretion_ignore`)
- [ ] 9.2 Implement replay queue drain loop per base connector contract
- [ ] 9.3 Write tests for filtered event persistence and replay

## 10. Docker and Deployment

- [ ] 10.1 Add `connector-home-assistant` service to `docker-compose.yml` with required environment variables
- [ ] 10.2 Configure service dependencies (Switchboard, PostgreSQL, CredentialStore availability)
- [ ] 10.3 Add health check endpoint configuration for the connector service

## 11. Home Butler System Prompt Updates

- [ ] 11.1 Update `roster/home/AGENTS.md` to include HA event response patterns (safety-critical, environmental drift, automation failure, routine acknowledgment)
- [ ] 11.2 Add HA event handling examples to the Home butler's interactive response mode documentation

## 12. Integration Testing

- [ ] 12.1 Write integration test: WebSocket connection lifecycle (connect, auth, subscribe, receive events, reconnect)
- [ ] 12.2 Write integration test: full pipeline (HA event -> filter -> envelope -> Switchboard submission)
- [ ] 12.3 Write integration test: REST fallback activation/deactivation during WebSocket outage
- [ ] 12.4 Write integration test: dashboard settings flow (validate, save, connector reads credentials)
