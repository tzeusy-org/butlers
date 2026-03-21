# Blob Storage
> **Purpose:** Swappable blob/attachment storage abstraction with a local filesystem backend for development.
> **Audience:** Contributors.
> **Prerequisites:** [Schema Topology](schema-topology.md).

## Overview

The blob storage layer (`src/butlers/storage/blobs.py`) provides an async interface for storing and retrieving binary media -- images, documents, audio files, and other attachments. It uses a protocol-based design so backends are swappable: the current `LocalBlobStore` implementation writes to the local filesystem, but the architecture supports future cloud backends (S3, MinIO, GCS) without changing consumer code. Every stored blob is identified by a URI-format storage reference string that encodes the backend scheme and the object key.

## Storage Reference Format

Every blob is identified by a `storage_ref` string in URI format:

```
<scheme>://<key>
```

For example: `local://2026/02/16/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jpg`

The `BlobRef` named tuple provides parsing and construction:

```python
ref = BlobRef.parse("local://2026/02/16/abc123.jpg")
# ref.scheme = "local", ref.key = "2026/02/16/abc123.jpg"

ref = BlobRef(scheme="local", key="2026/02/16/abc123.jpg")
ref.to_ref()  # "local://2026/02/16/abc123.jpg"
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

## Local Filesystem Backend

`LocalBlobStore` implements the protocol using the local filesystem, rooted at a configurable `base_dir`.

### Key Generation

Keys are date-partitioned with UUID4 suffixes to avoid collisions:

```
YYYY/MM/DD/<uuid4><extension>
```

The file extension is determined by: (1) the original filename's extension if provided, or (2) Python's `mimetypes.guess_extension()` based on the content type.

### Directory Layout

```
<base_dir>/
  2026/
    02/
      16/
        a1b2c3d4-e5f6-7890-abcd-ef1234567890.jpg
        b2c3d4e5-f6a7-8901-bcde-f12345678901.png
    03/
      01/
        c3d4e5f6-a7b8-9012-cdef-123456789012.pdf
```

Parent directories are created automatically on `put()`.

### Path Traversal Protection

When resolving a `storage_ref` to a filesystem path, the store resolves the path and verifies it falls within `base_dir`. Any path that escapes the root raises `ValueError` with a "Path traversal attempt detected" message:

```python
resolved_path = (self.base_dir / blob_ref.key).resolve()
resolved_path.relative_to(self.base_dir)  # Raises ValueError on traversal
```

The scheme is also validated -- a `LocalBlobStore` rejects references with schemes other than `"local"`.

## Error Handling

`BlobNotFoundError` is raised when `get()` or `delete()` targets a non-existent blob. It carries the `storage_ref` for diagnostic purposes. The `exists()` method catches `ValueError` (wrong scheme or traversal) and returns `False` rather than raising, so callers can safely probe across backends.

## Future Backends

The protocol-based design allows adding new backends without modifying consumers:

- **S3 / MinIO**: Would use `s3://bucket/key` references and the AWS SDK.
- **GCS**: Would use `gcs://bucket/key` references.

Consumers interact only with the `BlobStore` protocol and persist `storage_ref` strings in database columns. Switching backends requires only changing the store instance injected at startup.

## Related Pages

- [Schema Topology](schema-topology.md) -- Where blob references are stored in DB columns
- [Attachment Handling](../connectors/attachment-handling.md) -- How connectors use BlobStore for email attachments
