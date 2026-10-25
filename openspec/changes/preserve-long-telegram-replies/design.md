# Design

## Logical send and authority

Permission is checked once before traffic. The approved tool name, original text and stored argument digest remain unchanged; splitting is inside the owning Telegram adapter. A per-chat lock covers the complete sequence, releases on failure/cancellation and does not block another chat. Source chunks are lossless and conservatively bounded by 4000 UTF-16 units before rendering; markup can only reduce visible length. Paragraph, line, sentence and word boundaries are preferred. Fenced code fragments reopen as balanced `pre` elements; a single oversized code line is the only necessary mid-line split. A syntax-only closing fence never creates an empty request.

## Renderer and receipts

One tokenizer emits only balanced b/i/s/code/pre/a tags. Headings become bold lines; only HTTP(S) anchors activate; all other HTML/schemes stay literal. Inert sends bypass the renderer, retain raw source, use a per-chunk UTF-16 `pre` entity, and disable previews. Ordinary sends gain no preview override. Reply threading belongs only on the first chunk, keyboard controls only on the last. Each chunk needs `ok=true` and a positive exact-integer message ID. The last response is the logical receipt; multi-chunk results and audits retain every accepted ID. A partial error retains only confirmed prefix IDs and never claims the tail.

## Retry boundary

An explicit HTTP 400 parse-entities rejection permits one raw-source fallback for that chunk, and edits use the same fallback. No other 400 does. Actual 429 responses permit at most two retries from a finite numeric provider delay within a shared 30-second logical retry budget. Unknown receipts/timeouts/resets do not reconnect and replay an effect. Proven-not-attempted failures and ordinary legacy retry countercontrols remain distinct. Edits cannot append messages; raw source is conservatively clamped to 4000 UTF-16 units including `… (truncated)`.

## Terminal bookkeeping and recovery limits

Known prefix (`partial_delivery`) and actual Telegram-dispatch uncertainty (`telegram_effect_uncertain`) are distinct fixed server outcomes with `retryable=false`. Neither is delivery success. Messenger derives uncertainty from its actual selected adapter/transport, not a caller label. The cached route helper refuses effect reconnect after an uncertain call while retaining proven-not-attempted and read-only retries. Legacy candidate selection stops at the first terminal effect.

Each actual automatic consumer settles its existing store: deferred rows become cancelled without delivered credit; credential transitions cancel pending retry envelopes then write their existing debounce audit; Insight candidates become filtered with failure metadata and no engagement/cooldown; Health visit briefs retain a failure flag in the existing digest with no delivered timestamp. Attention records describe the failed tail. Approval recovery already persists provider-started/ambiguous handoffs and refuses a second provider attempt.

A process-local identity-bound latch fences a known terminal outcome before its existing-store write. Subsequent admissions reconcile that exact old work before fresh selection, including moved/expired windows; fresh unrelated rows are not cancelled. Failed writes retain the latch and cannot re-arm sends. Once the terminal store commits, its ordinary readback prevents replay after restart. An unresolved in-memory latch does not survive process death: this change provides no crash-atomic provider/database transaction, durable chunk outbox, or cross-process exactly-once promise. Ordinary legacy failures stay eligible for their existing retries.

## Verification and scope

Actual registered adapter/Messenger consumers, transport retry consumers and scheduled admission paths provide causal controls and restoration checks. Authored migrated PostgreSQL tests cover exact approved args/digest, independent terminal readback and authenticated recovery. Local collection is topology evidence; PostgreSQL bodies, hosted exact-head execution and protected merge evidence remain separately required. No live Telegram/model/provider traffic is used by software controls.
