## ADDED Requirements

### Requirement: Telegram file delivery

ID: REQ-module-telegram-004
Source: bu-z0b0ah.2
Scope: v1-mandatory

The Telegram module SHALL deliver attachments with multipart `sendPhoto` for image/jpeg, image/png and image/webp up to 10 MB, and `sendDocument` for everything else up to 50 MB. An explicit 400 photo-processing rejection (for example invalid dimensions) SHALL be re-sent once as a document. Every file SHALL be fetched and verified before the first Telegram call. When the message text renders to one chunk of at most 1024 UTF-16 code units and the first file has no caption of its own, the text SHALL be that file's caption. Otherwise the text SHALL be sent first through the ordered splitter, and the files SHALL follow as replies to its last chunk. Captions use the shared renderer, with one plain fallback on an explicit parse rejection. A failure after any accepted message SHALL be a terminal partial delivery. Audit rows SHALL carry media type, size, sha256 and provider message ids only.

#### Scenario: Long text with a document

- **WHEN** the message is longer than the caption limit and carries one PDF
- **THEN** the text is sent first and the PDF follows as a document replying to the last text message

#### Scenario: Upload fails after text

- **WHEN** the text was accepted and the file upload then fails
- **THEN** the module raises a non-retryable partial delivery carrying the accepted message id
