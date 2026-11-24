## ADDED Requirements

### Requirement: Ordered safe Telegram logical sends

ID: REQ-module-telegram-002
Source: bu-z0b0ah.1 original acceptance and RFC 0038 D11/D12
Scope: v1-mandatory

Telegram sends and replies SHALL split source losslessly into ordered chunks of at most 4000 visible UTF-16 units, preferring paragraphs, lines, sentences and words. Fenced code SHALL be reopened in balanced pre elements; only a single code line larger than the bound may be split mid-line. One safe renderer SHALL emit only balanced b/i/s/code/pre/a tags, bold headings and HTTP(S) anchors, escaping unknown markup. Inert delivery SHALL bypass rendering, retain raw source with one correctly measured pre entity per chunk and disable previews. Ordinary preview behavior SHALL remain unchanged.

Permission SHALL be checked once before the logical send and splitting SHALL occur after approval without changing the pinned text or argument digest. Each chunk SHALL require exact true ok and a positive exact-integer message_id. Reply threading SHALL appear only on the first chunk and reply markup only on the last. The returned last receipt and audit SHALL retain all accepted chunk IDs for a multi-chunk send. Per-chat locking SHALL prevent interleaving and release on exit.

An explicit parse-entities HTTP 400 SHALL permit one ordered plain-text fallback sequence of the chunk's exact original raw source, with every request bounded to at most 4000 UTF-16 units; source that fits SHALL use one request. The sequence SHALL retain the same chat lock, original deadline, shared rate-retry budget, first accepted reply reference, final keyboard and strict positive receipts. Plain requests SHALL NOT fall back again, accepted prefixes SHALL NOT be replayed, and other 400s SHALL NOT permit fallback. A rejected 429 SHALL permit at most two retries from finite numeric retry_after within a shared thirty-second logical retry budget. Timeout or uncertain receipt SHALL NOT authorize replay. A confirmed prefix SHALL raise a typed partial error containing only accepted IDs. Edits SHALL conservatively clamp to at most 4000 UTF-16 units including the truncation marker, SHALL NOT append messages, and explicit parse rejection SHALL permit only one bounded plain edit fallback.

#### Scenario: Long approved reply

- **WHEN** a pinned 10000-character reply is approved
- **THEN** three bounded ordered requests preserve the text and digest, first reply reference, last keyboard and all validated IDs

#### Scenario: Malformed markup and edit

- **WHEN** markup overlaps or an edit exceeds the single-message bound
- **THEN** HTML stays balanced or literal, only explicit parse rejection permits the ordered exact-source bounded send fallback, and edits retain the truncation marker with at most one bounded plain edit fallback

#### Scenario: Known prefix versus unknown receipt

- **WHEN** a later chunk fails after a receipt, or the first attempt times out without a receipt
- **THEN** known prefix IDs are retained only for the former and neither outcome authorizes an automatic replay
