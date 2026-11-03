## 1. Implementation
- [x] 1.1 `attachment_page_view` in `butlers.tools.attachments`: bounded render in the `_pdf_extract` child (`render` mode), shared concurrency slots, parent-side bound and PNG checks, typed refusals.
- [x] 1.2 Core media registration, D4 inventory declaration and dark-build hiding.

## 2. Verification
- [x] 2.1 Parametrised behavior test over a scanned page, an oversized media box, encrypted, out-of-range, non-PDF and foreign-prefix blobs.
- [x] 2.2 Inventory contract test: cataloged with D4 metadata, not listed to current sessions.
- [x] 2.3 Concurrency test: text and render children share the slot cap.
