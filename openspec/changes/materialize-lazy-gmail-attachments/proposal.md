# Fetch lazy Gmail attachments on request

## Why

The Gmail connector records most attachments as metadata-only `attachment_refs` rows, but nothing ever fetches them: `GmailConnector.fetch_attachment` has no caller, the connector was never given a blob store, and its unqualified `attachment_refs` SQL missed the `switchboard` schema on its search-path-less pool. A bill PDF that arrives by email therefore cannot be read, even with `attachment_read` (bu-jv1moa.48, slice S3 of the document-reading move after bu-jv1moa.4 and bu-jv1moa.47). The routed ATTACHMENTS prompt also called a lazy ref "could not be fetched", which is not what happened.

## What Changes

- New Switchboard tool `attachment_materialize(message_id, attachment_id)`. It marks the `attachment_refs` row requested and waits up to 20 seconds for the connector, then returns the blob ref (`status: "ready"`), a typed refusal (`attachment_not_found`, `attachment_too_large`, `fetch_failed`, `invalid_reference`) or `status: "pending"`. A fetched row returns its existing ref; asking again after a failure retries.
- The Gmail connector serves the requests: each account runtime polls every 2 seconds, claims up to two requested rows it owns with `FOR UPDATE SKIP LOCKED`, fetches them through `fetch_attachment` and records a failure. Only the connector holds Gmail credentials; Switchboard gains no egress.
- Migration `sw_055` adds `endpoint_identity`, `fetch_requested_at`, `fetch_claimed_at` and `fetch_error` to `attachment_refs`, with a partial index on pending requests.
- Fixes on the connector's existing path: SQL is schema-qualified; a replayed lazy ref no longer clears a fetched `blob_ref`; a download over the 25 MB global cap is refused; the connector resolves the connector blob store at start (shared helper with the Telegram connector), which also enables the specified eager `.ics` fetch.
- The ATTACHMENTS prompt lists a lazy ref as `status=not_fetched` and says it cannot be retrieved in this session. One builder serves the primary and recovery route paths.
- The tool declares RFC 0038 D4 metadata (effect class read, untrusted sensitive output, no recipient parameters) and lands dark: cataloged, not listed, until the goal-runtime cutover.

## Impact

Switchboard core tools (`switchboard_routing` group), the tool presentation inventory, the Gmail and Telegram connectors, the routed prompt builder and one Switchboard migration. No new dependency. Rollback is a revert plus the `sw_055` downgrade.
