## 1. Dependencies and Configuration

- [x] 1.1 Add `aioboto3` to `pyproject.toml` dependencies
- [x] 1.2 Replace `blob_storage_dir` in `ButlerConfig` with S3 fields (`endpoint_url`, `bucket`, `access_key_id`, `secret_access_key`, `region`) in `src/butlers/config.py`
- [x] 1.3 Update `butler.toml` parsing in `load_config()` to read `[butler.storage]` S3 fields instead of `blob_dir`
- [x] 1.4 Add `BLOB_S3_*` environment variable override resolution (env vars take precedence over TOML)
- [x] 1.5 Add `ConfigError` validation: `endpoint_url` and `bucket` are required

## 2. S3BlobStore Implementation

- [x] 2.1 Implement `S3BlobStore` class in `src/butlers/storage/blobs.py` with `aioboto3` session, implementing `put`, `get`, `delete`, `exists`
- [x] 2.2 Key generation: `{butler_name}/{YYYY}/{MM}/{DD}/{uuid}{ext}` with filename/content_type extension logic
- [x] 2.3 Scheme handling: `s3://{bucket}/{key}` for all blob refs
- [x] 2.4 Error mapping: S3 `NoSuchKey`/404 → `BlobNotFoundError`; wrong scheme in `exists()` → `False`
- [x] 2.5 Add `startup_check()` method that calls `head_bucket` to validate connectivity
- [x] 2.6 Add `close()` method for clean session teardown

## 3. Daemon Integration

- [x] 3.1 Update daemon blob store initialization in `daemon.py` to construct `S3BlobStore` from config
- [x] 3.2 Add S3 connectivity check (`startup_check()`) to daemon startup sequence, fail-fast on error
- [x] 3.3 Add `S3BlobStore.close()` call to daemon shutdown sequence
- [x] 3.4 Pass `butler_name` to `S3BlobStore` for key-prefix isolation
- [x] 3.5 Update `src/butlers/storage/__init__.py` exports: add `S3BlobStore`, remove `LocalBlobStore`

## 4. Cruft Cleanup

- [x] 4.1 Delete `LocalBlobStore` class from `src/butlers/storage/blobs.py`
- [x] 4.2 Remove `local://` scheme handling from `BlobRef` docstrings and comments
- [x] 4.3 Remove `blob_storage_dir` field from `ButlerConfig` dataclass
- [x] 4.4 Remove filesystem path-traversal guards (`_ref_to_path`, `resolve`, `relative_to`)
- [x] 4.5 Remove `data/blobs` default path references from config parsing
- [x] 4.6 Run `/cruft-cleanup` — verify zero references to `LocalBlobStore`, `blob_storage_dir`, `local://`, `data/blobs` in `src/`

## 5. Dev Environment

- [x] 5.1 Add MinIO service to `docker-compose.yml` with default bucket creation
- [x] 5.2 Update `roster/*/butler.toml` storage sections with S3-compatible config (env var references for credentials)
- [x] 5.3 Update `scripts/dev.sh` if needed to set `BLOB_S3_*` env vars for local MinIO

## 6. Migration Script

- [x] 6.1 Write `scripts/migrate_blobs_to_s3.py`: read `local://` refs from `attachment_refs`, upload files from `data/blobs/`, update DB to `s3://` refs
- [x] 6.2 Make script idempotent (skip already-migrated `s3://` refs)
- [x] 6.3 Handle missing local files gracefully (warn and skip, not fatal)
- [x] 6.4 Print summary: total, migrated, skipped, failed

## 7. Tests

- [x] 7.1 Add `moto` to dev dependencies in `pyproject.toml`
- [x] 7.2 Rewrite `tests/test_blob_storage.py` to test `S3BlobStore` against moto mock
- [x] 7.3 Add tests: put/get/delete/exists, key generation, extension handling, uniqueness, wrong-scheme handling, BlobNotFoundError
- [x] 7.4 Add tests: S3 connectivity check (startup_check success and failure)
- [x] 7.5 Update `tests/tools/test_attachments.py` fixtures to use S3-backed blob store (moto)
- [x] 7.6 Delete all `LocalBlobStore`-specific test cases (path traversal, filesystem assertions)

## 8. Documentation

- [x] 8.1 Update `docs/data_and_storage/blob-storage.md` — replace LocalBlobStore content with S3BlobStore usage, config examples, MinIO setup
- [x] 8.2 Update `Dockerfile` — remove any `data/blobs` volume assumptions
- [x] 8.3 Update `CLAUDE.md` if it references blob storage paths
