# Connector Documentation Changelog

This file tracks updates to connector documentation to ensure synchronization with implementation.

## 2026-02-16 - Heartbeat Protocol and Statistics

### Added
- **docs/connectors/heartbeat.md**: Normative specification for connector heartbeat protocol:
  - `connector.heartbeat.v1` envelope schema
  - 2-minute heartbeat interval with staleness thresholds (online/stale/offline)
  - Self-registration on first heartbeat (no manual pre-configuration)
  - Monotonic counter reporting (messages ingested, failed, source API calls)
  - Switchboard persistence: `connector_registry` + `connector_heartbeat_log`
  - Environment variables: `CONNECTOR_HEARTBEAT_INTERVAL_S`, `CONNECTOR_HEARTBEAT_ENABLED`

- **docs/connectors/statistics.md**: Normative specification for connector statistics aggregation and dashboard API:
  - Pre-aggregated rollup tables: hourly, daily, and fanout distribution
  - Rollup job scheduling (hourly at :05, daily at 00:15 UTC)
  - Retention and pruning policy: 7d raw heartbeats, 30d hourly, 1y daily
  - Dashboard API endpoints: list, detail, stats, summary, fanout
  - Frontend `/connectors` page spec: connector cards, volume charts, fanout matrix, error log
  - Pydantic response model definitions

### Updated
- **docs/connectors/interface.md**: Added sections 13 (Heartbeat Protocol) and 14 (Statistics and Dashboard Visibility) to connector responsibilities. Heartbeat is now a MUST requirement for all connectors.
- **docs/roles/switchboard_butler.md**: Added sections 17.5–17.7 covering connector heartbeat ingestion, statistics aggregation, and dashboard API ownership. Added connector tables to persistence surfaces (section 11).

### Documentation Sync Status Update

| Document | Implementation | Status | Notes |
|----------|---------------|--------|-------|
| `docs/connectors/heartbeat.md` | Not yet implemented | 📋 Spec ready | Awaiting implementation |
| `docs/connectors/statistics.md` | Not yet implemented | 📋 Spec ready | Awaiting implementation |

### Gaps Resolved
- **Connector Health Check Endpoint** (previously gap #7): Resolved by heartbeat protocol. Connectors report liveness via `connector.heartbeat` MCP tool rather than HTTP health endpoints.
- **Connector Metrics/Observability** (previously gap #4): Partially resolved. Heartbeat counters provide volume/error visibility. Prometheus metrics remain a separate concern for production monitoring.

## 2026-02-15 - Conformance Tests and Runbooks

### Added
- **tests/integration/test_connector_conformance.py**: End-to-end conformance tests covering:
  - Connector-to-ingest acceptance for Telegram and Gmail
  - Dedupe replay behavior validation
  - Downstream routing handoff structure verification
  - Checkpoint recovery testing
  - Error handling (HTTP errors, rate limits)

- **docs/runbooks/connector_operations.md**: Operational runbook covering:
  - Deployment modes (polling vs webhook for Telegram, watch+history for Gmail)
  - Checkpoint recovery procedures
  - Cutover operations from module-owned to connector-owned ingestion
  - Rollback operations
  - Monitoring and alerting guidance
  - Troubleshooting common issues
  - Complete environment variable reference

### Documentation Sync Status

| Document | Implementation | Status | Notes |
|----------|---------------|--------|-------|
| `docs/connectors/interface.md` | `src/butlers/connectors/*` | ✅ Synced | Contract matches implementation |
| `docs/connectors/telegram_bot.md` | `src/butlers/connectors/telegram_bot.py` | ✅ Synced | Polling and webhook modes documented |
| `docs/connectors/gmail.md` | `src/butlers/connectors/gmail.py` | ✅ Synced | Watch+history flow documented |
| `docs/connectors/connector_ingestion_migration_delta_matrix.md` | N/A | ✅ Current | Migration plan up-to-date |
| `docs/runbooks/connector_operations.md` | Runtime behavior | ✅ Synced | Covers deployed connector operations |

### Known Gaps and Follow-Up Items

The following gaps were identified during conformance test and documentation review:

1. **Telegram User Client Connector** (referenced in `docs/connectors/telegram_user_client.md`)
   - Status: DRAFT, not yet implemented
   - Scope: v2/gated feature requiring explicit user consent
   - Follow-up bead: butlers-zb7.5 (blocked, not yet ready)
   - Notes: User-client ingestion requires additional privacy controls beyond bot ingestion

2. **Discord Connector** (referenced in `docs/connectors/draft_discord.md`)
   - Status: DRAFT, v2-only
   - Scope: Future connector for Discord bot/webhook ingestion
   - Follow-up bead: TBD (not part of current epic)
   - Notes: Draft spec exists but no implementation planned for v1

3. **Ingest API Authentication**
   - Current state: Bearer token auth via `SWITCHBOARD_API_TOKEN`
   - Gap: No public documentation for token generation/rotation
   - Follow-up: Document token lifecycle in Switchboard API docs
   - Issue: butlers-zb7.2 covers API exposure but not auth lifecycle

4. **Connector Metrics/Observability**
   - Current state: Structured logging exists
   - Gap: No standardized metrics export (Prometheus, etc.)
   - Follow-up: Add metrics instrumentation for production monitoring
   - Priority: P2 (nice-to-have for v1, required for scale)

5. **Multi-Tenant Connector Deployment**
   - Current state: One connector instance per endpoint identity
   - Gap: No guidance for horizontal scaling with coordinated checkpointing
   - Follow-up: Document lease-based coordination for replicas
   - Priority: P3 (future enhancement)

6. **Gmail Watch Pub/Sub Integration**
   - Current state: Polling-based history fetch
   - Gap: No Pub/Sub push notification integration
   - Follow-up: Implement optional Pub/Sub mode for lower latency
   - Priority: P2 (optimization, polling works for v1)

7. **Connector Health Check Endpoint**
   - Current state: No HTTP health endpoint
   - Gap: Kubernetes readiness/liveness probes would fail
   - Follow-up: Add `/health` endpoint to connector runtimes
   - Priority: P1 for production deployment

### Conformance Test Coverage

✅ **Covered:**
- Telegram ingest acceptance and envelope structure
- Gmail ingest acceptance and envelope structure
- Dedupe replay behavior (both connectors)
- Routing handoff field validation
- Checkpoint recovery (restart scenarios)
- HTTP error handling (5xx, rate limits)

❌ **Not Covered (future work):**
- End-to-end routing verification (requires Switchboard integration)
- Long-running stability tests (crash recovery, multi-day runs)
- Webhook mode integration tests (require ngrok/public endpoint)
- Gmail OAuth token refresh edge cases
- Concurrent connector coordination tests
- Performance/load tests

### Next Steps

1. File follow-up beads for identified gaps (see above)
2. Run conformance tests as part of CI/CD pipeline
3. Monitor production connector deployments for undocumented edge cases
4. Update runbook with lessons learned from operational incidents

## 2026-02-16 - Telegram Media Download and Storage

### Added
- **Media download support in Telegram bot connector**: Extends connector to download and store media files (photos, documents, voice, video, stickers, etc.) via blob storage abstraction
  
- **Automatic image compression**: Images exceeding 5MB are automatically compressed with iterative quality reduction (85→75→65→55→45→35) to meet Claude API limits

- **Graceful degradation**: Media download failures log errors but do not block text ingestion, ensuring messages are always processed even if media fails

- **New connector methods**:
  - `_download_telegram_file(file_id)`: Downloads file from Telegram API
  - `_compress_image_if_needed(data, mime_type)`: Compresses large images to <5MB
  - `_store_media(data, content_type, filename, width, height)`: Stores media via BlobStore and returns attachment metadata

- **Blob storage integration**: 
  - Added `blob_store_dir` to `TelegramBotConnectorConfig`
  - Default blob storage directory: `.blobs/`
  - Configurable via `CONNECTOR_BLOB_STORE_DIR` environment variable

- **Dependencies**:
  - Added `pillow>=10.0.0` for image compression

### Modified
- **`_normalize_to_ingest_v1` method**: 
  - Now async (was sync)
  - Extracts media from all supported Telegram media types
  - Populates `attachments` field in IngestPayloadV1 envelope
  - Extracts caption text for media messages

- **Supported media types**:
  - Photo (array, uses largest size)
  - Document (PDFs, etc.)
  - Voice messages
  - Video
  - Audio
  - Stickers
  - Animations (GIFs)
  - Video notes (circular videos)

### Test Coverage
- Photo message with attachment validation
- Document message with correct MIME type
- Text-only messages (no attachments)
- Graceful degradation on download failures
- Image compression for files >5MB
- Dimensions and metadata preservation

### Implementation Details

**Media extraction flow:**
1. Check message for media keys (photo, document, voice, etc.)
2. Extract file_id and metadata (MIME type, filename, dimensions)
3. Download file via Telegram getFile API
4. Compress images >5MB with iterative quality reduction
5. Store via BlobStore (LocalBlobStore implementation)
6. Build IngestAttachment metadata with storage_ref
7. Add to envelope's `payload.attachments` field

**Compression strategy:**
- Only images are compressed (non-images pass through)
- Target size: 5MB (Claude API limit)
- Iterative quality reduction: 85, 75, 65, 55, 45, 35
- Uses Pillow JPEG compression with `optimize=True`
- If target not reached at quality 35, uses best effort

**Error handling:**
- Download failures logged and tracked via metrics
- Text ingestion continues even if media fails
- Metrics record `media_download_errors` and `media_storage_errors`

### Documentation Sync Status

| Document | Status | Notes |
|----------|--------|-------|
| `docs/connectors/telegram_bot.md` | ⚠️ Needs update | Media download flow not yet documented |
| `src/butlers/connectors/telegram_bot.py` | ✅ Synced | Implementation includes docstrings |
| `roster/switchboard/tools/routing/contracts.py` | ✅ Synced | IngestAttachment contract already defined |

### Follow-Up Items

1. Update `docs/connectors/telegram_bot.md` with media download flow
2. Document blob storage configuration in connector operations runbook
3. Add metrics dashboard for media download/storage operations
4. Consider adding blob storage garbage collection for orphaned files
5. Add integration test for end-to-end media flow (Telegram → BlobStore → Claude API)

