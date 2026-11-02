## ADDED Requirements

### Requirement: PDF page image view

ID: REQ-core-daemon-010
Source: bu-jv1moa.47; RFC 0016; RFC 0036; RFC 0038 D4, D6 and the dark build
Scope: v1-mandatory

The `media` core group SHALL include `attachment_page_view(storage_ref, page = 1)`. It SHALL apply the same prefix scope, 20 MB size cap and PDF checks as `attachment_read`, render the one requested page locally with no external egress, and return page metadata with `content_trust = "untrusted"` together with the page as a PNG image content block, never as a base64 field in JSON. Rendering SHALL run in the same kind of child process as `attachment_read` extraction, with no inherited environment, a capped address space and a wall-clock timeout, and SHALL share its concurrency slots. The render scale SHALL be fixed from the page size before drawing so that the image is at most 1568 pixels on its long edge and at most 1,150,000 pixels in area; the daemon SHALL take the image dimensions from the PNG's IHDR chunk and SHALL refuse any child output whose IHDR dimensions differ from its header, are below 1 pixel or exceed those bounds, that lacks a PNG signature, that names a page other than the one requested, or that exceeds 4 MB. A child that breaches its memory or time limit SHALL yield the refusal `extraction_limit_exceeded`, and the daemon SHALL keep running. A non-PDF blob, an encrypted or corrupt PDF, an oversized blob, a page below 1 or past the end, and a ref outside the caller's prefixes SHALL each return a typed refusal (`status = "refused"` with a `reason`) and no image. The tool SHALL declare D4 metadata with effect class `read`, untrusted sensitive output and no recipient parameters. Until the RFC 0038 cutover it SHALL be in the daemon's tool catalog but disabled on the FastMCP server; presenting it SHALL additionally require a model with a passing RFC 0036 exact-path vision proof.

#### Scenario: A scanned page is viewed

- **WHEN** `attachment_read` marks page 2 of a stored PDF `no_text_layer` and `attachment_page_view` is called for page 2
- **THEN** the result carries untrusted page metadata and a PNG image of page 2 within the pixel bounds

#### Scenario: An oversized page

- **WHEN** the page's media box is 200 by 200 inches
- **THEN** the image is still at most 1568 pixels on its long edge and 1,150,000 pixels in area

#### Scenario: A page past the end

- **WHEN** the requested page is greater than the page count
- **THEN** the tool returns `status = "refused"` with reason `invalid_page_range` and no image

#### Scenario: Dark before the cutover

- **WHEN** a butler daemon finalizes its tool catalog with the `media` group enabled
- **THEN** the catalog contains `attachment_page_view` with its D4 metadata and the FastMCP tool listing does not
