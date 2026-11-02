# Design

## Surface

`notify(attachments=[{filename, media_type, storage_ref | content_text, caption?}])` applies to Telegram `send`/`reply` only. Limits: at most 10 files; inline text at most 256 KB and one of text/plain, text/csv, text/markdown, text/calendar or application/json; each blob at most 50 MB. A `storage_ref` must parse as `s3://<deployment bucket>/<key>`. Shape checks run before any I/O.

## Pinning and replay

notify() fetches every blob through the daemon BlobStore and computes sha256 and size before any recipient gate, park or send. The pinned items ride on the recipient-gate arguments (`notify`) and on the `notify.v1` envelope, so Messenger's parked `route.execute` arguments carry them too. The Telegram adapter fetches and verifies every file again, before its first provider call. A missing blob, an unavailable store or a digest or size mismatch sends nothing (`attachment_unavailable`, `attachment_digest_mismatch`). An approved replay can therefore send only the reviewed bytes.

## Telegram delivery

Images (jpeg, png, webp, at most 10 MB) use multipart `sendPhoto`; everything else uses `sendDocument`. When the text renders to a single chunk of at most 1024 UTF-16 units and the first file has no caption of its own, the text becomes that file's HTML caption. A caption parse rejection falls back once to plain text. Otherwise the text goes first through the existing splitter, and the files follow as replies to its last chunk. 429 responses use the existing bounded retry. A failure after any accepted message is the existing terminal partial delivery. The receipt is the last message id, and all ids are audited.

## Exposure and channels

File contents cannot be made inert. When `render_links_inert` is set and attachments are present, Messenger parks for per-event owner approval even for the owner (REQ-core-notify-033 applied to files). On approved inert replay, the text and any item captions go through the inert text path, and the files are sent without captions. Email and WhatsApp raise `unsupported_attachment` before approval or provider traffic.

## Logging

Audit rows (`telegram_send_files`) carry media type, size, sha256 and provider message ids. Switchboard's notification log redacts `content_text` and keeps refs, types, sizes and digests.

## Non-goals

Files above the Bot API limits; a general blob-writing tool; email or WhatsApp attachments; email-attachment storage_refs (waits on bu-jv1moa.4).
