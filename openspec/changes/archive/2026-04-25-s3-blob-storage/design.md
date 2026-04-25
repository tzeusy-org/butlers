## Context

Blob storage is currently backed by `LocalBlobStore` — a filesystem implementation that writes date-partitioned files under `data/blobs/`. The `BlobStore` protocol already defines a clean async interface (`put`, `get`, `delete`, `exists`), and all consumers depend on the protocol, not the concrete class. The operator runs an S3-compatible object store (MinIO) on their NAS and wants to eliminate local filesystem state to enable Kubernetes deployment.

Current call graph:
- `daemon.py` instantiates `LocalBlobStore(Path(config.blob_storage_dir))` at startup
- `daemon.py` exposes `get_attachment` MCP tool → calls `tools/attachments.py`
- `connectors/gmail.py` calls `blob_store.put()` for eager-fetched attachments
- `attachment_refs.blob_ref` column stores `local://...` URI strings

## Goals / Non-Goals

**Goals:**
- Replace `LocalBlobStore` with `S3BlobStore` implementing the same `BlobStore` protocol
- Target any S3-compatible API (MinIO, Garage, SeaweedFS, etc.) — not AWS-specific
- Configure via `butler.toml` `[butler.storage]` and environment variable overrides
- Remove `LocalBlobStore` and all filesystem-specific code paths (cruft cleanup)
- Provide a migration path for existing `local://` blob refs in the database

**Non-Goals:**
- Multi-backend support (no registry of backends, no runtime switching) — S3 is the only backend
- Presigned URL support or direct-to-client streaming — blobs are always proxied through the butler
- Lifecycle policies, versioning, or cross-region replication — these are configured on the object store itself
- Changing the `BlobStore` protocol interface — `put`/`get`/`delete`/`exists` signatures stay identical

## Decisions

### 1. Use `aioboto3` for the S3 client

**Choice:** `aioboto3` (async wrapper over `boto3`)

**Rationale:** The `BlobStore` protocol methods are `async`. Using `aioboto3` keeps S3 I/O non-blocking in the asyncio event loop. `boto3` is the de facto standard for S3-compatible APIs, and `aioboto3` wraps it with proper async context managers. The `endpoint_url` parameter in boto3 already supports pointing at any S3-compatible endpoint — no custom code needed.

**Alternatives considered:**
- `boto3` with `asyncio.to_thread()` — works but adds thread pool overhead and loses backpressure
- `aiobotocore` directly — lower-level, no resource-level API; `aioboto3` wraps it cleanly
- `minio-py` — MinIO-specific; we want generic S3 compatibility

### 2. Single bucket, key-prefix isolation per butler

**Choice:** All butlers share one bucket. Keys are prefixed with the butler name: `{butler_name}/{date_partition}/{uuid}{ext}`.

**Rationale:** Keeps deployment simple (one bucket to provision). Butler-level isolation comes from the key prefix, matching the existing schema-based DB isolation pattern. The bucket name is configured once globally, not per butler.

**Alternatives considered:**
- One bucket per butler — more IAM isolation but operationally heavier for a single-user system
- Flat keys (no butler prefix) — loses the ability to scope/audit per butler

### 3. Credential resolution: env vars → butler.toml → IAM role

**Choice:** S3 credentials are resolved in priority order:
1. Environment variables (`BLOB_S3_ENDPOINT_URL`, `BLOB_S3_ACCESS_KEY_ID`, `BLOB_S3_SECRET_ACCESS_KEY`, `BLOB_S3_BUCKET`, `BLOB_S3_REGION`)
2. `butler.toml` `[butler.storage]` section
3. Fall through to boto3's default credential chain (IAM roles, `~/.aws/credentials`, etc.)

**Rationale:** Env vars take precedence for container orchestration (K8s Secrets). TOML for dev convenience. Boto3 default chain as fallback covers AWS IAM roles and developer workstations with `aws configure`.

### 4. `s3://` scheme in BlobRef, hard cutover

**Choice:** `BlobRef` produces `s3://{bucket}/{key}` URIs. No `local://` support retained.

**Rationale:** The proposal explicitly calls for cruft removal. Keeping `local://` as a fallback creates a dual-path maintenance burden. A one-time migration script converts existing `attachment_refs.blob_ref` values.

**Alternatives considered:**
- Scheme registry with pluggable backends — over-engineering for a single-user system
- Keep `local://` as readonly fallback — defeats the purpose of the migration

### 5. Connection lifecycle: session-per-daemon, not per-request

**Choice:** Create one `aioboto3.Session` at daemon startup, reuse it for all blob operations during the daemon's lifetime. The session is closed during daemon shutdown.

**Rationale:** `aioboto3` sessions are lightweight but creating a new client per request adds connection negotiation overhead. One session per daemon matches the `asyncpg` pool pattern already used for PostgreSQL.

### 6. Remove `blob_storage_dir` config entirely

**Choice:** Delete `blob_storage_dir` from `ButlerConfig` and `butler.toml` parsing. Replace with `[butler.storage]` fields: `endpoint_url`, `bucket`, `access_key_id`, `secret_access_key`, `region`.

**Rationale:** `blob_storage_dir` is filesystem-specific. The new fields map directly to boto3's `create_client('s3', ...)` parameters. No backward compatibility needed — this is a breaking change.

## Risks / Trade-offs

**[Risk] S3-compatible endpoint unavailable at startup** → The daemon should fail fast with a clear error if the S3 endpoint is unreachable during initialization (attempt a `head_bucket` call). This prevents silent blob failures during runtime.

**[Risk] Existing `local://` blob refs become dangling** → The migration script must be run before the new daemon version starts. Document this as a required migration step. The script reads `local://` refs from `attachment_refs.blob_ref`, uploads the corresponding files from `data/blobs/` to S3, and updates the refs to `s3://`.

**[Risk] Large blob uploads blocking the event loop** → `aioboto3` handles this natively with async I/O. For very large blobs (>5MB), boto3's multipart upload kicks in automatically. Current max blob size is 25MB (Gmail's ceiling).

**[Trade-off] No local development fallback** → Developers must run MinIO (or equivalent) locally. A `docker-compose` service for MinIO is trivial to add and keeps the dev experience consistent with production.

**[Trade-off] Network latency vs filesystem I/O** → S3-compatible API adds network round-trip vs local disk read. For a NAS on the same LAN, latency is negligible. The architectural benefit (stateless containers) outweighs the latency cost.

## Migration Plan

1. Add `aioboto3` dependency to `pyproject.toml`
2. Implement `S3BlobStore` in `src/butlers/storage/blobs.py`
3. Delete `LocalBlobStore` class and all filesystem-specific code
4. Update `ButlerConfig` and `butler.toml` parsing for new storage fields
5. Update daemon initialization to construct `S3BlobStore`
6. Add MinIO service to `docker-compose.yml` for dev
7. Write migration script: `scripts/migrate_blobs_to_s3.py` — reads `local://` refs, uploads files, updates DB
8. Update all tests to use moto (S3 mock) or a test MinIO instance
9. Run `/cruft-cleanup` to verify no residual `local://`, `LocalBlobStore`, or `blob_storage_dir` references remain
10. Update documentation in `docs/data_and_storage/blob-storage.md`

**Rollback:** If S3 migration fails mid-way, the migration script is idempotent — re-run uploads only un-migrated refs. To fully rollback, revert the code change and restore `data/blobs/` from backup.

## Open Questions

None — the design is straightforward given the existing protocol abstraction.
