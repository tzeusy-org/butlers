## 1. Implementation
- [x] 1.1 `attachment_read` in `butlers.tools.attachments`: pypdfium2 extraction, page and character bounds, typed refusals, RFC 0016 prefix scoping.
- [x] 1.2 Core media registration, D4 inventory declaration and dark-build hiding in catalog finalization.

## 2. Verification
- [x] 2.1 Parametrised behavior test over paged text, no-text pages, encrypted, corrupt, non-PDF, out-of-range and foreign-prefix blobs.
- [x] 2.2 Inventory contract test: cataloged with D4 metadata, not listed to current sessions.
