## ADDED Requirements

### Requirement: Media attachment reads are scoped to the caller's blob prefix

ID: REQ-core-daemon-009
Source: bu-jv1moa.50; RFC 0016
Scope: v1-mandatory

The `media` core tools `get_attachment` and `attachment_view` SHALL serve only a `storage_ref` under the calling butler's RFC 0016 prefix `{bucket}/{butler}/` or the connector prefix `{bucket}/connectors/`, where the calling butler is the daemon's own name and never a tool argument. Any other well-formed ref SHALL return a typed refusal (`status = "refused"` with reason `attachment_forbidden`) carrying no blob data, and the tool SHALL NOT fetch that blob from the store. `get_attachment`'s 64 KB inline contract SHALL NOT change.

#### Scenario: Another butler's blob is refused

- **WHEN** a butler calls `get_attachment` or `attachment_view` with a ref under another butler's prefix
- **THEN** the tool returns `status = "refused"` with reason `attachment_forbidden`, no blob data, and makes no fetch from the blob store
