## 1. Implementation
- [x] 1.1 Shared attachment validation, fetch and digest pinning (`butlers.core.notify_attachments`).
- [x] 1.2 notify() `attachments` parameter, pinned on recipient-gate args and the notify.v1 envelope.
- [x] 1.3 notify.v1 `NotifyAttachmentV1`; Switchboard notification log redaction.
- [x] 1.4 Messenger `telegram_send_files` command, `unsupported_attachment` on other channels, inert parking.
- [x] 1.5 Telegram `sendPhoto`/`sendDocument` multipart delivery with caption rules and content-free audit.

## 2. Verification
- [x] 2.1 Module behavior tests with a fake BlobStore and mocked httpx.
- [x] 2.2 Messenger route, approval-park and digest re-verification tests.
- [ ] 2.3 Live dev-stack check that a real blob arrives as a Telegram document (owner-run).
