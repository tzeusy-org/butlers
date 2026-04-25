# S3-Compatible Blob Storage

## Purpose
Defines the blob storage backend that all butlers use for binary object persistence (attachments, media). All blob I/O goes through an S3-compatible API (MinIO, Garage, SeaweedFS, AWS S3, etc.). There is no local filesystem blob storage.

## ADDED Requirements

### Requirement: S3BlobStore implements BlobStore protocol
`S3BlobStore` SHALL implement the `BlobStore` protocol (`put`, `get`, `delete`, `exists`) using an S3-compatible API via `aioboto3`. It is the only `BlobStore` implementation in the codebase.

#### Scenario: put stores object and returns s3:// ref
- **WHEN** `put(data, content_type="image/jpeg", filename="photo.jpg")` is called
- **THEN** the blob SHALL be uploaded to the configured S3 bucket
- **AND** the object key SHALL be `{butler_name}/{YYYY}/{MM}/{DD}/{uuid}.jpg` (date-partitioned, butler-prefixed)
- **AND** the return value SHALL be `s3://{bucket}/{key}`
- **AND** the `ContentType` metadata on the S3 object SHALL match the provided `content_type`

#### Scenario: put generates unique keys
- **WHEN** two `put` calls are made with identical data, content_type, and filename
- **THEN** each SHALL produce a distinct storage_ref (UUID-based uniqueness)

#### Scenario: put with filename extension priority
- **WHEN** `filename` is provided and has an extension
- **THEN** that extension SHALL be used for the object key
- **WHEN** `filename` is absent or has no extension
- **THEN** the extension SHALL be inferred from `content_type` via `mimetypes.guess_extension()`

#### Scenario: get retrieves object by storage_ref
- **WHEN** `get("s3://bucket/butler/2026/03/21/abc.jpg")` is called and the object exists
- **THEN** the binary content of the S3 object SHALL be returned

#### Scenario: get raises BlobNotFoundError for missing object
- **WHEN** `get` is called with a storage_ref whose S3 key does not exist (404/NoSuchKey)
- **THEN** `BlobNotFoundError` SHALL be raised with the storage_ref

#### Scenario: delete removes object
- **WHEN** `delete` is called with a valid storage_ref for an existing object
- **THEN** the S3 object SHALL be deleted

#### Scenario: delete raises BlobNotFoundError for missing object
- **WHEN** `delete` is called with a storage_ref whose S3 key does not exist
- **THEN** `BlobNotFoundError` SHALL be raised

#### Scenario: exists returns boolean
- **WHEN** `exists` is called with a storage_ref for an existing object
- **THEN** it SHALL return `True`
- **WHEN** `exists` is called with a storage_ref for a non-existent object
- **THEN** it SHALL return `False`

#### Scenario: exists returns False for wrong scheme
- **WHEN** `exists` is called with a `local://` or other non-`s3://` scheme
- **THEN** it SHALL return `False` (no exception)

### Requirement: BlobRef uses s3:// scheme exclusively
`BlobRef` SHALL produce and parse `s3://{bucket}/{key}` URIs. The `local://` scheme is not supported.

#### Scenario: BlobRef roundtrip
- **WHEN** `BlobRef(scheme="s3", key="mybucket/general/2026/03/21/abc.jpg")` is created
- **THEN** `to_ref()` SHALL return `"s3://mybucket/general/2026/03/21/abc.jpg"`
- **AND** `BlobRef.parse("s3://mybucket/general/2026/03/21/abc.jpg")` SHALL produce the same BlobRef

#### Scenario: BlobRef rejects invalid format
- **WHEN** `BlobRef.parse("not-a-ref")` is called
- **THEN** `ValueError` SHALL be raised

### Requirement: S3 storage configuration
S3 connection parameters SHALL be configurable via `butler.toml` `[butler.storage]` section and environment variable overrides.

#### Scenario: Configuration from butler.toml
- **WHEN** `butler.toml` contains:
  ```toml
  [butler.storage]
  endpoint_url = "http://nas.local:9000"
  bucket = "butlers"
  region = "us-east-1"
  access_key_id = "${BLOB_S3_ACCESS_KEY_ID}"
  secret_access_key = "${BLOB_S3_SECRET_ACCESS_KEY}"
  ```
- **THEN** `S3BlobStore` SHALL be configured with those values
- **AND** `${VAR}` references SHALL be resolved from the environment (existing env-var resolution)

#### Scenario: Environment variable overrides
- **WHEN** `BLOB_S3_ENDPOINT_URL`, `BLOB_S3_BUCKET`, `BLOB_S3_ACCESS_KEY_ID`, `BLOB_S3_SECRET_ACCESS_KEY`, `BLOB_S3_REGION` are set
- **THEN** they SHALL override the corresponding `butler.toml` values

#### Scenario: Fallback to boto3 default credential chain
- **WHEN** `access_key_id` and `secret_access_key` are not set in TOML or environment
- **THEN** `aioboto3` SHALL fall through to its default credential chain (IAM roles, `~/.aws/credentials`, etc.)

#### Scenario: Missing endpoint_url blocks startup
- **WHEN** `endpoint_url` is not set in TOML or environment
- **THEN** daemon startup SHALL fail with a `ConfigError` indicating the missing field

#### Scenario: Missing bucket blocks startup
- **WHEN** `bucket` is not set in TOML or environment
- **THEN** daemon startup SHALL fail with a `ConfigError` indicating the missing field

### Requirement: S3 connectivity validation at startup
The daemon SHALL validate S3 connectivity during initialization before accepting requests.

#### Scenario: Successful connectivity check
- **WHEN** the daemon starts and can reach the S3 endpoint and the bucket exists
- **THEN** startup proceeds normally

#### Scenario: Unreachable endpoint fails startup
- **WHEN** the S3 endpoint is unreachable during startup
- **THEN** the daemon SHALL fail with a clear error message including the endpoint URL

#### Scenario: Missing bucket fails startup
- **WHEN** the S3 endpoint is reachable but the configured bucket does not exist
- **THEN** the daemon SHALL fail with an error message indicating the bucket name

### Requirement: S3 session lifecycle
The `S3BlobStore` SHALL manage an `aioboto3` session tied to the daemon lifecycle.

#### Scenario: Session created at startup
- **WHEN** the daemon initializes blob storage
- **THEN** an `aioboto3.Session` SHALL be created and an S3 client resource opened

#### Scenario: Session closed at shutdown
- **WHEN** the daemon shuts down
- **THEN** the S3 client resource SHALL be closed cleanly

### Requirement: Blob ref migration tooling
A migration script SHALL convert existing `local://` blob refs to `s3://` refs.

#### Scenario: Migration uploads and re-refs
- **WHEN** `scripts/migrate_blobs_to_s3.py` is run
- **THEN** for each row in `attachment_refs` where `blob_ref` starts with `local://`:
  1. The corresponding file SHALL be read from the local `data/blobs/` directory
  2. The file SHALL be uploaded to S3 with the new key format
  3. The `blob_ref` column SHALL be updated to the new `s3://` URI
- **AND** the script SHALL be idempotent (re-running skips already-migrated refs)

#### Scenario: Migration handles missing local files
- **WHEN** a `local://` ref points to a file that no longer exists on disk
- **THEN** the script SHALL log a warning and skip that ref (not fatal)

#### Scenario: Migration reports summary
- **WHEN** the migration completes
- **THEN** it SHALL print: total refs found, successfully migrated, skipped (already migrated), failed (missing files)

### Requirement: LocalBlobStore and local:// cruft removal
All code paths related to `LocalBlobStore` and `local://` scheme SHALL be removed.

#### Scenario: No LocalBlobStore in codebase
- **WHEN** the change is complete
- **THEN** there SHALL be no `LocalBlobStore` class definition anywhere in `src/`
- **AND** there SHALL be no imports of `LocalBlobStore`
- **AND** `src/butlers/storage/__init__.py` SHALL not export `LocalBlobStore`

#### Scenario: No blob_storage_dir config
- **WHEN** the change is complete
- **THEN** `ButlerConfig` SHALL not have a `blob_storage_dir` field
- **AND** `butler.toml` parsing SHALL not read `blob_dir` or `blob_storage_dir`

#### Scenario: No local:// scheme handling
- **WHEN** the change is complete
- **THEN** no code in `src/` SHALL reference the `"local"` scheme string for blob storage
- **AND** no code SHALL contain filesystem path-traversal guards for blob refs

#### Scenario: No data/blobs directory assumption
- **WHEN** the change is complete
- **THEN** no code in `src/` SHALL reference `data/blobs` as a default or fallback path

#### Scenario: Tests updated
- **WHEN** the change is complete
- **THEN** `tests/test_blob_storage.py` SHALL test `S3BlobStore` (not `LocalBlobStore`)
- **AND** all blob-related test fixtures SHALL use moto or equivalent S3 mock
- **AND** tests for `LocalBlobStore` SHALL be deleted
