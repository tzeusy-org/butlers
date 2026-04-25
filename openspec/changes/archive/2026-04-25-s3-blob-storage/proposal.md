## Why

Blob storage currently writes to the local filesystem (`data/blobs/`). This makes the system non-portable to container orchestrators (Kubernetes) — pod eviction or rescheduling loses all stored attachments. The operator runs an S3-compatible object store (e.g. MinIO) on their NAS and wants all blob I/O to go through the S3 API, eliminating local filesystem state entirely.

## What Changes

- **BREAKING**: Remove `LocalBlobStore` and all `local://` scheme handling. The only supported blob backend will be S3-compatible object storage.
- Add `S3BlobStore` implementing the existing `BlobStore` protocol, using `boto3` (or `aioboto3`) with configurable endpoint URL, bucket, credentials, and region.
- Replace `blob_storage_dir` config with S3-compatible connection parameters in `butler.toml` (`[butler.storage]` section) and environment variables.
- Update `BlobRef` to use `s3://` scheme exclusively. Existing `local://` refs in the `attachment_refs.blob_ref` column become invalid — provide a one-time migration tool.
- Run cruft cleanup: remove `LocalBlobStore` class, `blob_storage_dir` config field, filesystem path-traversal logic, date-partitioned directory creation, and all associated tests. No backward-compatibility shims.
- Update daemon initialization to construct `S3BlobStore` instead of `LocalBlobStore`.
- Update Gmail connector and attachment tools — no interface changes needed (they consume the `BlobStore` protocol), but imports and test fixtures change.

## Capabilities

### New Capabilities
- `s3-blob-storage`: S3-compatible blob storage backend — connection config, `S3BlobStore` implementation, credential resolution, bucket lifecycle, and migration tooling for existing `local://` refs.

### Modified Capabilities
- `core-daemon`: Daemon blob store initialization changes from filesystem path to S3 client construction.
- `connector-gmail`: No behavioral change, but test fixtures and integration tests must use S3-compatible storage instead of temp directories.

## Impact

- **Code**: `src/butlers/storage/blobs.py` (rewrite), `src/butlers/config.py` (storage config), `src/butlers/daemon.py` (init), `src/butlers/connectors/gmail.py` (test fixtures only)
- **Dependencies**: Add `aioboto3` (async S3 client) to `pyproject.toml`
- **Config**: `butler.toml` `[butler.storage]` section gains `endpoint_url`, `bucket`, `access_key_id`, `secret_access_key`, `region` fields. `blob_storage_dir` removed.
- **Environment variables**: `BLOB_S3_ENDPOINT_URL`, `BLOB_S3_BUCKET`, `BLOB_S3_ACCESS_KEY_ID`, `BLOB_S3_SECRET_ACCESS_KEY`, `BLOB_S3_REGION` as overrides.
- **Database**: `attachment_refs.blob_ref` values change scheme from `local://` to `s3://`. One-time migration script needed.
- **Tests**: All blob storage tests rewritten against S3-compatible API (moto or localstack in CI). `LocalBlobStore` tests deleted.
- **Docker**: Container images no longer need writable `data/blobs/` volume. S3 endpoint must be reachable.
- **Cruft removed**: `LocalBlobStore` class, `blob_storage_dir` config, filesystem date-partitioning logic, path-traversal guards, `local://` scheme in `BlobRef`.
