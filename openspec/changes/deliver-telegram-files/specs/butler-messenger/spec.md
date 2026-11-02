## ADDED Requirements

### Requirement: Attachment delivery and pinned replay

ID: REQ-butler-messenger-006
Source: bu-z0b0ah.2; REQ-butler-messenger-003; owner decision 9
Scope: v1-mandatory

Messenger SHALL deliver `notify.v1` attachments only on Telegram, through the `telegram_send_files` command. Its rule-matching arguments carry refs, media types and digests, never inline text. Email and WhatsApp deliveries with attachments SHALL fail with `unsupported_attachment` before approval evaluation or provider traffic. When `render_links_inert` is true and attachments are present, Messenger SHALL park the delivery for per-event owner approval, whatever the recipient, because file contents cannot be rendered inert. A non-owner delivery with attachments SHALL park under the existing recipient gate with every digest in the parked arguments. Execution, including approved replay, SHALL re-read every blob and refuse a digest or size mismatch before any send.

#### Scenario: Blob changed after approval was requested

- **WHEN** a parked non-owner Telegram delivery with a storage_ref attachment is approved after the blob's bytes changed
- **THEN** execution returns attachment_digest_mismatch and nothing is sent

#### Scenario: Inert-required delivery with a file

- **WHEN** an exposed owner notification carries an attachment
- **THEN** Messenger parks it for owner approval and sends nothing until approved
