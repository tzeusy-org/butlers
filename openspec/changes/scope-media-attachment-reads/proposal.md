# Scope media attachment reads to the caller's blob prefix

## Why

`get_attachment` and `attachment_view` fetched any `storage_ref` they were given, so a butler could read another butler's blobs. The RFC 0016 key prefix is the blob isolation boundary, and `attachment_read` and notify attachments already enforce it (bu-jv1moa.50, found during bu-jv1moa.4).

## What Changes

- `get_attachment` and `attachment_view` check every ref against the calling butler's prefix `{bucket}/{butler}/` and the connector prefix `{bucket}/connectors/` before fetching. A ref outside both returns the typed refusal `attachment_forbidden` and is never fetched.
- A malformed ref keeps its existing behavior. `get_attachment`'s 64 KB inline contract is unchanged.

## Impact

Core media tool group only. No migration, no new table and no blob writes. A blob stored under a key outside both prefixes becomes unreadable through these tools; `S3BlobStore` writes every blob under its butler's prefix. Rollback is a revert.
