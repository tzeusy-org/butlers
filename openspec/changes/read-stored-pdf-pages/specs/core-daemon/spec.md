## ADDED Requirements

### Requirement: Paged PDF attachment read

ID: REQ-core-daemon-008
Source: bu-jv1moa.4; RFC 0016; RFC 0038 D4, D6 and the dark build
Scope: v1-mandatory

The `media` core group SHALL include `attachment_read(storage_ref, page_start = 1, page_count = 10)`. It SHALL read a stored PDF blob of at most 20 MB under the calling butler's RFC 0016 prefix `{bucket}/{butler}/` or the connector prefix `{bucket}/connectors/`, extract text locally with no external egress, and return at most 20 pages and 16,000 characters of page-numbered text with `next_page_start` naming the first page not returned. Every result SHALL label the text `content_trust = "untrusted"` and carry it only as JSON string data. A page with no text layer SHALL return empty text with `no_text_layer = true` and a hint that a page image is needed. A non-PDF blob, an encrypted or corrupt PDF, an oversized blob, a page range past the end and a ref outside those prefixes SHALL each return a typed refusal (`status = "refused"` with a `reason`) and no page text. The tool SHALL declare D4 metadata with effect class `read`, untrusted sensitive output and no recipient parameters. Until the RFC 0038 cutover it SHALL be in the daemon's tool catalog but disabled on the FastMCP server, so classify-and-route sessions can neither list nor call it. `get_attachment`'s 64 KB inline contract SHALL NOT change.

#### Scenario: A large bill is read by page

- **WHEN** a 40-page text PDF over 64 KB is read from page 3
- **THEN** the result carries page-numbered text starting at page 3, at most 16,000 characters, and `next_page_start` after the last returned page

#### Scenario: A scanned page

- **WHEN** a requested page has no text layer
- **THEN** that page returns empty text with `no_text_layer = true` and a page-image hint

#### Scenario: An encrypted PDF

- **WHEN** the stored PDF is password-protected
- **THEN** the tool returns `status = "refused"` with reason `encrypted_pdf` and no text

#### Scenario: Dark before the cutover

- **WHEN** a butler daemon finalizes its tool catalog with the `media` group enabled
- **THEN** the catalog contains `attachment_read` with its D4 metadata and the FastMCP tool listing does not
