# Render scanned PDF pages as images

## Why

`attachment_read` returns no text for a scanned page (`no_text_layer`), so a scanned bill or travel document stays unreadable even though the PDF is stored (bu-jv1moa.47, slice S2 of the document-reading move after bu-jv1moa.4).

## What Changes

- New core media tool `attachment_page_view(storage_ref, page)`. It renders one page of a stored PDF to a PNG locally with pypdfium2 (no egress) and returns page metadata labelled untrusted plus the image as an MCP image content block, never a base64 JSON field.
- Rendering runs in the same resource-limited child as `attachment_read` (no inherited environment, 512 MB address space, 15 s) and shares its two concurrency slots. The scale is chosen from the page size before drawing, so the bitmap is at most 1568 px on the long edge and 1,150,000 px in area at no more than 200 dpi. The parent re-checks the child's dimensions, PNG signature and a 4 MB output cap.
- Scope, size, media and page refusals match `attachment_read` (`attachment_forbidden`, `unsupported_media_type`, `attachment_too_large`, `encrypted_pdf`, `corrupt_pdf`, `invalid_page_range`, `extraction_limit_exceeded`, `extraction_busy`). `attachment_read`'s `no_text_layer` hint now names the tool.
- The tool declares RFC 0038 D4 metadata (effect class read, untrusted sensitive output, no recipient parameters) and lands dark like `attachment_read`. Because its output is vision input, presenting it also waits on a model with a passing RFC 0036 exact-path vision proof (bu-s1u4br); this change does not present it anywhere.

## Impact

Core media tool group, the tool presentation inventory and the PDF child protocol (now a `text` or `render` mode). No new dependency (Pillow and pypdfium2 are already direct dependencies), no migration and no blob writes. Rollback is a revert.
