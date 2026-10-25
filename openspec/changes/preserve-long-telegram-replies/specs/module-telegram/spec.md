## ADDED Requirements

### Requirement: Ordered safe Telegram logical sends

ID: REQ-module-telegram-002
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

Telegram sends and replies SHALL split source losslessly into ordered chunks of at most 4000 visible UTF-16 units, preferring paragraphs, lines, sentences and words. Fenced code SHALL be reopened in balanced pre elements; only a single code line larger than the bound may be split mid-line. One safe renderer SHALL emit only balanced b/i/s/code/pre/a tags, bold headings and HTTP(S) anchors, escaping unknown markup. Inert delivery SHALL bypass rendering, retain raw source with one correctly measured pre entity per chunk and disable previews. Ordinary preview behavior SHALL remain unchanged.

Permission SHALL be checked once before the logical send and splitting SHALL occur after approval without changing the pinned text or argument digest. Each chunk SHALL require exact true ok and a positive exact-integer message_id. Reply threading SHALL appear only on the first chunk and reply markup only on the last. The returned last receipt and audit SHALL retain all accepted chunk IDs for a multi-chunk send. Per-chat locking SHALL prevent interleaving and release on exit.

An explicit parse-entities HTTP 400 SHALL permit exactly one raw-source fallback; other 400s SHALL NOT. A rejected 429 SHALL permit at most two retries from finite numeric retry_after within a shared thirty-second logical retry budget. Timeout or uncertain receipt SHALL NOT authorize replay. A confirmed prefix SHALL raise a typed partial error containing only accepted IDs. Edits SHALL conservatively clamp to at most 4000 UTF-16 units including the truncation marker and use the same parse fallback.

#### Scenario: Long approved reply

- **WHEN** a pinned 10000-character reply is approved
- **THEN** three bounded ordered requests preserve the text and digest, first reply reference, last keyboard and all validated IDs

#### Scenario: Malformed markup and edit

- **WHEN** markup overlaps or an edit exceeds the single-message bound
- **THEN** HTML stays balanced or literal, edits retain the truncation marker, and only explicit parse rejection permits one raw fallback

#### Scenario: Known prefix versus unknown receipt

- **WHEN** a later chunk fails after a receipt, or the first attempt times out without a receipt
- **THEN** known prefix IDs are retained only for the former and neither outcome authorizes an automatic replay
