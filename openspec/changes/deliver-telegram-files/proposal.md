# Deliver files to the owner on Telegram

## Why

Butlers can read a file the owner sends on Telegram but can never hand one back. Telegram is the owner's phone surface, and epic bu-z0b0ah requires that the owner can receive a file (bu-z0b0ah.2).

## What Changes

- `notify()` gains an optional `attachments` list. Each item is an existing blob (`storage_ref`, RFC 0016) or a small inline generated text file (`content_text`: CSV, iCalendar, Markdown, JSON or plain text).
- `notify.v1` `delivery` gains an optional `attachments` field. notify() fetches every file, pins its sha256 and size, and carries the pins on approval arguments and the envelope.
- Messenger delivers Telegram attachments through a new adapter path (`telegram_send_files`). Images go out as photos and everything else as documents, with the message text as caption when it fits. Email and WhatsApp refuse attachments with `unsupported_attachment`.
- Inert-required delivery with files parks for per-event owner approval. Approved replay re-reads each blob and refuses changed bytes.

## Impact

notify core tool, notify.v1 contract, Messenger route execution, the Telegram module, the Switchboard notification log (inline text is redacted) and the notify catalog declaration (`attachments` is a resource parameter). No migration, no new table and no new blob-writing tool. Rollback is a revert.
