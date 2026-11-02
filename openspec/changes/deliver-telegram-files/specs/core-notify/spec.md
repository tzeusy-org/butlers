## ADDED Requirements

### Requirement: Owner file attachments on notify

ID: REQ-core-notify-041
Source: bu-z0b0ah.2; RFC 0016; RFC 0038 D4
Scope: v1-mandatory

`notify()` SHALL accept an optional `attachments` list of at most 10 items. Each item names `filename`, `media_type` and exactly one of `storage_ref` (an existing blob in the deployment's bucket) or `content_text` (at most 256 KB; text/plain, text/csv, text/markdown, text/calendar or application/json), plus an optional `caption`. Attachments SHALL be accepted only for Telegram `send` or `reply`. Other channels SHALL return `unsupported_attachment`. Before any recipient gate, park or delivery, notify() SHALL fetch every file and pin its sha256 digest and size on the recipient-gate arguments and on the `notify.v1` envelope. An invalid item, a missing blob or an unavailable store SHALL return an error and send nothing. Recipient resolution and approval rules are unchanged. File bytes and inline text SHALL NOT appear in logs, audit rows, spans or the Switchboard notification log.

#### Scenario: Owner receives a generated CSV

- **WHEN** a session calls notify with a short message and an inline text/csv attachment for the owner on Telegram
- **THEN** the envelope carries the attachment with its sha256 and size, and the owner receives one document named as given with the message as caption

#### Scenario: Missing blob

- **WHEN** one attachment's storage_ref names no stored blob
- **THEN** notify returns attachment_unavailable and nothing is delivered
