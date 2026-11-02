## ADDED Requirements

### Requirement: On-demand fetch of lazy attachment refs

ID: REQ-connector-gmail-004
Source: bu-jv1moa.48; connector-gmail Attachment Handling (lazy fetch); RFC 0016; RFC 0038 D4, D6 and the dark build
Scope: v1-mandatory

Switchboard SHALL provide `attachment_materialize(message_id, attachment_id)`, which marks the matching `switchboard.attachment_refs` row requested and waits a bounded time for it to be fetched. Only the Gmail connector account that recorded the row (or any account, for a row with no recorded account) SHALL fetch it, by claiming requested unfetched rows without blocking other claimers, downloading through the Gmail API, storing the bytes under the connector blob prefix and setting `fetched` and `blob_ref`; a failed download, or a failure to record the stored blob on the row, SHALL be recorded on the row, and bytes the row does not name SHALL be deleted; claims an interrupted batch did not finish SHALL be released for the next poll. Switchboard SHALL NOT call the Gmail API itself. The tool SHALL return `status = "ready"` with the blob ref, media type, filename, size and `content_trust = "untrusted"`; `status = "refused"` with reason `attachment_not_found`, `fetch_failed` or `invalid_reference`; or `status = "pending"` when the wait ends first. A fetched row SHALL return its existing blob ref without a download, a request after `fetch_failed` SHALL retry, and re-recording a lazy ref on ingest replay SHALL NOT clear a fetched blob ref. A download larger than the global attachment cap SHALL fail. The tool SHALL declare D4 metadata with effect class `read`, untrusted sensitive output and no recipient parameters, and until the RFC 0038 cutover it SHALL be in Switchboard's tool catalog but disabled on its FastMCP server. Until then the routed ATTACHMENTS section SHALL list a lazy ref as `status=not_fetched`, distinct from a failed fetch, and say it cannot be retrieved in that session.

#### Scenario: A lazy PDF is fetched once

- **WHEN** `attachment_materialize` is called for a lazy ref recorded by a running connector account
- **THEN** the connector downloads and stores it and the tool returns `status = "ready"` with an `s3://` ref under the connector prefix
- **AND** a second call returns the same ref with no second download

#### Scenario: Another account's ref

- **WHEN** a ref was recorded by account A and only account B's runtime is polling
- **THEN** B does not claim it and the tool returns `status = "pending"`

#### Scenario: A failed fetch

- **WHEN** the Gmail download fails
- **THEN** the tool returns `status = "refused"` with reason `fetch_failed`
- **AND** a later call requests the fetch again

#### Scenario: Dark before the cutover

- **WHEN** Switchboard finalizes its tool catalog
- **THEN** the catalog contains `attachment_materialize` with its D4 metadata and the FastMCP tool listing does not
