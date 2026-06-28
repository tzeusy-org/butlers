# RFC 0016: S3 Blob Storage Contract

**Status:** Accepted
**Date:** 2026-04-25

## Summary

All binary object persistence in Butlers (attachments, media) goes through a
single S3-compatible blob store. The `S3BlobStore` class implements the
`BlobStore` protocol using `aioboto3` against any S3-compatible endpoint
(MinIO, Garage, SeaweedFS, AWS S3, etc.). There is no local filesystem blob
storage. Keys are butler-prefixed and date-partitioned. Credentials are
resolved exclusively from the `CredentialStore` (DB-backed, no env-var
fallback). Blob storage is initialized at daemon startup phase 8c; a
`head_bucket` check confirms reachability when possible. If the configured
endpoint or bucket cannot be validated, startup continues with blob storage
disabled and blob-dependent tools fail clearly at runtime. A single bucket
serves all blob types for the deployment; no attachment-vs-export bucket split
exists.

## Motivation

As butlers add attachment-bearing channels (email, Telegram, Google Health,
media ingestion) the number of code paths touching blob I/O grows. Without a
design contract at the RFC level the following risks accumulate:

1. Modules disagree on key format, producing non-recoverable `storage_ref`
   values after rename or migration.
2. Credential resolution for S3 parameters diverges between modules (some
   might attempt env-var fallback, others CredentialStore-only).
3. Retention semantics are undocumented, leaving operators unsure whether to
   configure object lifecycle rules on the bucket.
4. Error handling contracts are unclear, causing modules to swallow
   `BlobNotFoundError` differently or re-raise with inconsistent context.

This RFC locks down the data-plane contract so every module and tool that
touches blob I/O can treat `S3BlobStore` as a stable, documented boundary.

## Design

### D1: Bucket Layout

The deployment uses a **single bucket** for all blob types. There is no
attachment-vs-export bucket split and no per-butler bucket. Butler-level
isolation is enforced via key prefix (see D2).

The bucket name is operator-configured via `BLOB_S3_BUCKET` in the
`CredentialStore`. The same bucket receives:

- Inbound attachments (email, Telegram, Google Health, etc.)
- Outbound exports (if any butler writes export blobs)
- Any other binary payloads persisted by butler modules

A multi-bucket split (e.g. `attachments-bucket` vs `exports-bucket`) is a
possible future evolution if retention policy requirements diverge materially.
For now, a single bucket with prefix-based organization and uniform lifecycle
policy is sufficient and simpler to operate.

### D2: Key Naming Convention

Every key stored by `S3BlobStore` follows the format:

```
{butler_name}/{YYYY}/{MM}/{DD}/{uuid}{ext}
```

Examples:
- `general/2026/04/25/3fa85f64-5717-4562-b3fc-2c963f66afa6.jpg`
- `email/2026/04/25/7c4f1d2e-9b3a-48e0-a1f7-1234567890ab.pdf`

Properties:
- **Butler prefix** — key starts with the butler's name, providing logical
  isolation without requiring per-butler buckets.
- **Date partition** — `{YYYY}/{MM}/{DD}` in UTC. Supports lifecycle rules
  keyed on prefix age and enables time-range S3 inventory queries.
- **UUID uniqueness** — UUID v4 guarantees distinct keys even when content,
  content-type, and filename are identical.
- **Extension** — derived from the provided `filename` (priority) or
  inferred via `mimetypes.guess_extension(content_type)`. May be absent if
  both sources yield nothing.

Storage references use the `s3://` scheme:

```
s3://{bucket}/{butler_name}/{YYYY}/{MM}/{DD}/{uuid}{ext}
```

The bucket is embedded in the ref so refs remain valid if the endpoint URL
changes (e.g. MinIO → AWS migration). Refs are stored in `attachment_refs`
and any other butler table that persists blob locations; they are treated as
opaque URIs outside of `S3BlobStore`.

### D3: Retention and Lifecycle Policy

No automatic expiry is enforced by the application layer. Retention is an
**operator responsibility** configured via S3 bucket lifecycle rules.

Guidance by use case:

| Blob type | Recommended lifecycle rule |
|-----------|---------------------------|
| Email / Telegram attachments | Retain indefinitely (personal archive) or set a rolling window (e.g. 1 year) based on operator preference |
| Health media (images, audio) | Retain indefinitely unless the owning butler tombstones the record |
| Exports / generated reports | Retain indefinitely; may be pruned manually |

Because the application never auto-deletes blobs (except when an explicit
`BlobStore.delete` call is made by a module), there is no risk of an
application lifecycle rule conflicting with bucket rules. Operators may safely
add S3 lifecycle expiry rules to the `{butler_name}/` prefix of departed
butlers without impacting active ones.

A future RFC or capability spec MAY add application-level TTL tracking in
`attachment_refs` if retention requirements diverge; this RFC makes no such
commitment.

### D4: BlobStore Protocol Contract

`BlobStore` is a structural `Protocol` defined in
`src/butlers/storage/blobs.py`. All methods are `async`.

```python
class BlobStore(Protocol):
    async def put(self, data: bytes, *, content_type: str,
                  filename: str | None = None) -> str: ...
    async def get(self, storage_ref: str) -> bytes: ...
    async def delete(self, storage_ref: str) -> None: ...
    async def exists(self, storage_ref: str) -> bool: ...
```

**`put(data, *, content_type, filename=None) -> str`**
- Stores `data` in S3 under a newly generated key.
- Returns a `s3://` storage ref.
- Sets `ContentType` metadata on the S3 object.
- If `filename` carries an extension, that extension is used; otherwise the
  extension is inferred from `content_type` via `mimetypes.guess_extension`.
- Never re-uses an existing key; each call produces a distinct ref.

**`get(storage_ref: str) -> bytes`**
- Parses the `s3://` ref, fetches the object body, returns raw bytes.
- Raises `BlobNotFoundError(storage_ref)` when the S3 key does not exist
  (error codes `NoSuchKey` or `404`).
- Other S3 / network errors propagate as-is.

**`delete(storage_ref: str) -> None`**
- Checks object existence with `head_object` first (S3 delete is idempotent
  but the protocol requires a not-found signal).
- Raises `BlobNotFoundError(storage_ref)` if the key does not exist.
- Deletes the object if present.

**`exists(storage_ref: str) -> bool`**
- Returns `True` if the key exists, `False` if not.
- Returns `False` (no exception) for refs with an unrecognized scheme (e.g.
  a stale `local://` ref encountered during migration).
- Other S3 / network errors propagate as-is.

**`BlobNotFoundError`**
- Subclass of `Exception`.
- Carries `storage_ref: str` attribute.
- Message format: `"Blob not found: {storage_ref}"`.
- Callers MUST NOT swallow this error silently; surface it as a user-facing
  error or log it at WARNING level and return a meaningful response.

### D5: Credential Resolution Path

All five S3 parameters are resolved from the layered `CredentialStore`
(DB-backed, per-butler then global). There is no `[butler.storage]` TOML
section, no `butler.toml` keys for S3 settings, and **no `os.environ`
fallback**.

| Credential key | Required | Default if absent |
|----------------|----------|-------------------|
| `BLOB_S3_ENDPOINT_URL` | Yes (non-fatal if missing) | `None` — blob storage disabled |
| `BLOB_S3_BUCKET` | Yes (non-fatal if missing) | `None` — blob storage disabled |
| `BLOB_S3_REGION` | No | `"us-east-1"` |
| `BLOB_S3_ACCESS_KEY_ID` | No | Falls through to `aioboto3` default credential chain (IAM roles, `~/.aws/credentials`) |
| `BLOB_S3_SECRET_ACCESS_KEY` | No | Falls through to `aioboto3` default credential chain |

All `resolve()` calls are made with `env_fallback=False`. The dashboard
secrets UI at `/secrets` is the only supported way to set these values.

If `BLOB_S3_ENDPOINT_URL` or `BLOB_S3_BUCKET` is absent, the daemon logs a
warning directing the operator to configure credentials at `/secrets` and sets
`daemon.blob_store = None`. Startup continues. Blob operations at runtime fail
with a clear error (not a silent no-op).

### D6: Startup Check Semantics

Phase 8c of the daemon startup sequence (after CredentialStore creation at
phase 8b) initializes blob storage:

1. Resolve all five credential keys.
2. If endpoint or bucket is absent → log warning, set `daemon.blob_store = None`, continue.
3. Otherwise, construct `S3BlobStore` and call `await startup_check()`.
4. `startup_check()` performs `head_bucket` on the configured bucket.
   - **Success** → logs `"S3 blob storage ready: endpoint=... bucket=... prefix=..."` and continues.
   - **Bucket missing** (404 / `NoSuchBucket`) → raises `BlobStorageStartupError`
     with message including bucket name and endpoint. The daemon catches this,
     logs a warning, sets `daemon.blob_store = None`, and continues.
   - **Endpoint unreachable** (connection error) → raises `BlobStorageStartupError`
     with message including endpoint URL. The daemon catches this, logs a
     warning, sets `daemon.blob_store = None`, and continues.

Phase 8c is non-fatal for both "credentials absent" and "configured but
unavailable" branches. This keeps text-only routing, scheduling, and normal MCP
tools available when the blob service is down. Blob-producing and blob-consuming
tools must surface the unavailable store as an actionable runtime error rather
than silently dropping media.

### D7: Session Lifecycle

`S3BlobStore` holds an `aioboto3.Session`. S3 clients are opened and closed
per-call via context managers; no persistent connection is held between
operations. This design is safe for long-lived daemon processes.

At daemon shutdown (step 6b of the graceful shutdown sequence), `close()` is
called on the blob store. The method exists for lifecycle symmetry and to
allow future cleanup hooks; in the current implementation it is a no-op
because per-call context managers already release resources.

### D8: Scheme Contract — s3:// Only

The `s3://` scheme is the only supported scheme. The `local://` scheme and
`LocalBlobStore` implementation have been removed. No code in `src/` references
`"local"` as a blob scheme, `blob_storage_dir`, or the `data/blobs/` path.

`BlobRef.parse` rejects any storage_ref string without a `://` separator
(`ValueError`). `exists()` silently returns `False` for non-`s3://` refs,
allowing migration tooling to probe legacy refs without raising.

The migration script `scripts/migrate_blobs_to_s3.py` converts existing
`local://` refs in `attachment_refs` to `s3://` refs. The script is
idempotent (skips already-migrated rows) and reports a summary of totals
(migrated, skipped, failed).

## Non-Goals

- Multi-bucket layout per blob type or per butler. A single operator-managed
  bucket is the supported topology.
- Application-layer TTL enforcement. Retention is an operator concern via
  S3 bucket lifecycle rules.
- Streaming large blobs. `put` and `get` read full payloads into memory.
  Large-blob streaming is a future concern if media sizes grow significantly.
- Pre-signed URL generation. Not currently in scope; could be added to the
  protocol without breaking existing callers.
- Cross-butler blob sharing. Storage refs are butler-prefixed; cross-butler
  access would require direct S3 access outside the `BlobStore` protocol.

## Topology References

- `about/lay-and-land/components.md` §High-level View — MinIO / S3 appears as
  a core dependency of the daemon.
- `about/lay-and-land/dependencies.md` §Dependency Severity Table — MinIO / S3
  rated as a degraded dependency: attachment operations fail when the store is
  down; core messaging continues.
- `about/lay-and-land/dependencies.md` §"MinIO/S3 down" — documents the blast
  radius at the topology level.

## References

- RFC 0001 (daemon lifecycle) — startup phases including 8b (CredentialStore
  creation) and 8c (blob storage init). Graceful shutdown step 6b (blob store
  close).
- RFC 0006 (database schema and isolation) — `attachment_refs` table stores
  `storage_ref` values produced by this contract.
- `openspec/specs/s3-blob-storage/spec.md` — normative capability spec; this
  RFC codifies its wire contract at the design tier.
- `src/butlers/storage/blobs.py` — implementation of `BlobRef`, `BlobStore`,
  `BlobNotFoundError`, and `S3BlobStore`.
- `src/butlers/lifecycle.py` — phase 8c implementation.
- `scripts/migrate_blobs_to_s3.py` — migration tooling for existing `local://`
  refs.
