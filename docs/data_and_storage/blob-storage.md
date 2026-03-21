# Blob Storage
> **Purpose:** S3-compatible blob storage for all butler media and attachments.
> **Audience:** Contributors.
> **Prerequisites:** [Schema Topology](schema-topology.md).

## Overview

The blob storage layer (`src/butlers/storage/blobs.py`) provides an async interface for storing and retrieving binary media — images, documents, audio files, and other attachments. All blob I/O goes through an S3-compatible API (Garage, MinIO, AWS S3, etc.). The `S3BlobStore` implementation uses `aioboto3` with path-style addressing. Every stored blob is identified by a URI-format storage reference string.

## Storage Reference Format

Every blob is identified by a `storage_ref` string in URI format:

```
s3://<bucket>/<butler_name>/<YYYY>/<MM>/<DD>/<uuid><ext>
```

For example: `s3://butlers-blobs/general/2026/02/16/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jpg`

The `BlobRef` named tuple provides parsing and construction:

```python
ref = BlobRef.parse("s3://butlers-blobs/general/2026/02/16/abc123.jpg")
# ref.scheme = "s3", ref.key = "butlers-blobs/general/2026/02/16/abc123.jpg"

ref = BlobRef(scheme="s3", key="butlers-blobs/general/2026/02/16/abc123.jpg")
ref.to_ref()  # "s3://butlers-blobs/general/2026/02/16/abc123.jpg"
```

`BlobRef.parse()` raises `ValueError` if the `://` separator is missing.

## BlobStore Protocol

The `BlobStore` protocol defines four async operations that any backend must implement:

| Method | Signature | Description |
|---|---|---|
| `put` | `(data, *, content_type, filename=None) -> str` | Store binary data, return a `storage_ref` string. `content_type` determines the file extension; `filename` provides an optional hint. |
| `get` | `(storage_ref) -> bytes` | Retrieve blob data. Raises `BlobNotFoundError` if not found. |
| `delete` | `(storage_ref) -> None` | Remove a blob. Raises `BlobNotFoundError` if not found. |
| `exists` | `(storage_ref) -> bool` | Check existence. Returns `False` for wrong-scheme references rather than raising. |

## S3BlobStore

`S3BlobStore` implements the protocol using any S3-compatible object store.

### Key Generation

Keys are butler-prefixed and date-partitioned with UUID4 suffixes:

```
<butler_name>/<YYYY>/<MM>/<DD>/<uuid4><extension>
```

The file extension is determined by: (1) the original filename's extension if provided, or (2) Python's `mimetypes.guess_extension()` based on the content type.

### Configuration

All S3 parameters are managed via the dashboard secrets UI at `/secrets`. No environment variables or `butler.toml` fields are needed.

| Secret Key | Description | Sensitive | Required |
|---|---|---|---|
| `BLOB_S3_ENDPOINT_URL` | S3-compatible endpoint URL | No | Yes |
| `BLOB_S3_BUCKET` | Bucket name | No | Yes |
| `BLOB_S3_REGION` | Region (e.g. `garage`, `us-east-1`) | No | No (default: `us-east-1`) |
| `BLOB_S3_ACCESS_KEY_ID` | Access key ID | Yes | No (falls through to boto3 default chain) |
| `BLOB_S3_SECRET_ACCESS_KEY` | Secret access key | Yes | No |

Secrets are stored in the `butler_secrets` table and resolved at daemon startup via the `CredentialStore` (DB-only, no env-var fallback).

### Startup Validation

At daemon startup (step 8c, after CredentialStore is built), the daemon:
1. Resolves all S3 parameters from the CredentialStore
2. Validates that `BLOB_S3_ENDPOINT_URL` and `BLOB_S3_BUCKET` are set
3. Calls `head_bucket` to verify the endpoint is reachable and the bucket exists

Failure blocks startup with a clear error message.

### Dev Environment (MinIO)

`docker-compose.yml` includes a MinIO service with auto-created `butlers-blobs` bucket. Seed the S3 secrets via the dashboard after first start:

```bash
docker compose up -d postgres minio minio-setup
# Then open the dashboard and configure S3 secrets at /secrets
```

### Production (Garage on NAS)

Configure via the dashboard secrets UI at `/secrets`:

- `BLOB_S3_ENDPOINT_URL` = `http://tzehouse-synology.parrot-hen.ts.net:3900`
- `BLOB_S3_BUCKET` = `butlers-blobs`
- `BLOB_S3_REGION` = `garage`
- `BLOB_S3_ACCESS_KEY_ID` / `BLOB_S3_SECRET_ACCESS_KEY` from Bitwarden

## Error Handling

`BlobNotFoundError` is raised when `get()` or `delete()` targets a non-existent blob. It carries the `storage_ref` for diagnostic purposes. The `exists()` method catches `ValueError` (wrong scheme) and returns `False` rather than raising.

## Related Pages

- [Schema Topology](schema-topology.md) — Where blob references are stored in DB columns
- [Attachment Handling](../connectors/attachment-handling.md) — How connectors use BlobStore for email attachments
