# Read stored PDFs by page

## Why

A butler cannot read a stored PDF over about 48 KB: `get_attachment` embeds blobs as base64 only up to its 64 KB inline cap, so bills, statements and travel documents the owner sends are unreadable (bu-jv1moa.4, slice S1 of lens-documents#1 in the 2026-10-15 Dots superset pursuit).

## What Changes

- New core media tool `attachment_read(storage_ref, page_start, page_count)`. It extracts page-numbered text from a stored PDF locally with pypdfium2 (no egress), at most 20 pages and 16,000 characters a call, with a `next_page_start` hint.
- Typed refusals for a non-PDF, an encrypted or corrupt PDF, a PDF over 20 MB, a page range past the end and a ref outside the caller's RFC 0016 prefix. A page with no text layer returns a "page image needed" hint.
- The tool declares RFC 0038 D4 metadata (effect class read, untrusted sensitive output, no recipient parameters). It lands dark: the daemon catalogs it, then disables it on FastMCP so today's classify-and-route sessions can neither list nor call it until the goal-runtime cutover presents it.
- `get_attachment`'s 64 KB inline contract is unchanged.

## Impact

Core media tool group, the tool presentation inventory and the daemon's catalog finalization. New dependency pypdfium2. No migration, no new table and no blob writes. Rollback is a revert.

Later slices (separate beads): page images for scans, `attachment_materialize` for lazy Gmail refs, and document deadlines.
