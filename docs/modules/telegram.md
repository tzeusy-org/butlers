# Telegram Module

> **Purpose:** Output-only Telegram integration providing send, reply, and reaction tools via the Telegram Bot API.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Telegram module provides MCP tools for sending messages, replying to messages, and reacting to messages via the Telegram Bot API. It is **output-only** -- message ingestion is handled separately by `TelegramBotConnector`, which submits inbound messages through the canonical ingest API.

Like the Email module, the Telegram module supports two credential scopes (**user** and **bot**) and resolves credentials from the database at startup.

The module also provides an internal `react_for_ingest()` method used by the daemon to fire lifecycle reaction emojis on inbound messages as they flow through the pipeline (eyes on receive, thumbs-up on success, space invader on error).

Source: `src/butlers/modules/telegram.py`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.telegram]
webhook_url = "https://example.com/telegram/webhook"  # optional

[modules.telegram.user]
enabled = false
token_env = "USER_TELEGRAM_TOKEN"

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"
```

### Credential Resolution

- **User scope**: Token resolved from owner entity's `public.entity_info` (type `telegram_bot_token`).
- **Bot scope**: Token resolved via `CredentialStore` (DB-first with env fallback).

### Webhook Setup

When `webhook_url` is configured, the module calls the Telegram `setWebhook` API at startup. When omitted, no webhook is set (suitable for polling-based setups).

## Tools Provided

| Tool | Description |
|------|-------------|
| `telegram_send_message` | Send a message to a chat by chat ID |
| `telegram_reply_to_message` | Reply to a specific message by chat ID and message ID |
| `telegram_react_to_message` | React to a message with an arbitrary emoji |
| `telegram_edit_message_text` | Edit the text of a message the bot already sent; a Telegram 400 returns `{"edited": false, "error_code": 400}` instead of raising |

## Text rendering and long replies

Send and reply share one safe renderer and lossless source splitter. Each request conservatively holds at most 4000 UTF-16 source units, so the visible text stays below Telegram's limit. Splits prefer paragraphs, lines, sentences, then words. Fenced code reopens as a balanced `pre` block in each fragment; only an individually oversized code line is hard-split. The renderer supports bold, italic, strikethrough, inline/fenced code, bold headings and HTTP(S) Markdown anchors. Unknown HTML, other URL schemes, and malformed markers remain escaped/literal.

A per-chat lock keeps a logical send's chunks together. The first chunk carries reply threading; the final chunk carries its keyboard. Each chunk must have `ok=true` and a positive integer `message_id`. A multi-chunk response returns the last chunk's response with every accepted ID in `result.message_ids`; audits retain all IDs. Splitting happens after the existing exact-text approval and once-per-send permission check.

An explicit HTTP 400 parse-entities rejection permits one ordered fallback sequence containing that chunk's exact original raw source without parse mode. Each request holds at most 4000 UTF-16 units; source that fits uses one request. Other 400s are not retried. Actual rejected 429s allow at most two provider-declared retries inside a shared 30-second retry budget. Timeouts and unknown receipts do not authorize effect replay. If a later chunk fails, `TelegramPartialDeliveryError` carries only confirmed prefix IDs, with audit category `telegram_partial_delivery`. Messenger exposes `partial_delivery=true, retryable=false`. A first unknown effect is distinct `telegram_effect_uncertain=true`, without invented prefix IDs.

Automatic deferred/Secrets/Insight/Health consumers record the failed tail in their existing terminal stores and do not resend the original text. A failed bookkeeping operation leaves a process-local exact-work fence; subsequent admissions settle that old work before fresh selection. After settlement, the existing stored terminal state prevents restart replay. The unresolved latch does not survive process death: there is no new durable chunk outbox or crash-atomic provider/database guarantee. Ordinary legacy failures and proven-not-attempted/read-only retries retain their existing behavior. Approval recovery retains its existing durable ambiguous-handoff fence.

Edits cannot add messages. They clamp source conservatively to 4000 UTF-16 units including `… (truncated)`, use the same renderer and at most one bounded plain edit after explicit parse rejection, and retain the existing structured refusal for other 400s. Inert sends bypass rendering, split raw text with a per-chunk UTF-16 `pre` entity and keep previews disabled. Ordinary sends gain no `link_preview_options` override.

## Lifecycle Reactions

The module defines three reaction constants used by the daemon's ingest pipeline:

| Constant | Emoji | When Used |
|----------|-------|-----------|
| `REACTION_IN_PROGRESS` | Eyes | Message received, processing started |
| `REACTION_SUCCESS` | Thumbs up | Pipeline completed successfully |
| `REACTION_FAILURE` | Space invader | Pipeline error |

The `react_for_ingest()` method parses the envelope's reply target (`event.reply_target_ref`, format `"<chat_id>:<message_id>"`; its parameter keeps the historical name `external_thread_id`) and sets the appropriate reaction. Failures are silently logged -- reaction errors never block message processing.

## Implementation Details

The module uses `httpx.AsyncClient` for all Telegram API calls. The client is created at startup and closed on shutdown. API calls go to `https://api.telegram.org/bot{token}/`.

Helper functions for parsing Telegram update payloads (`_extract_text`, `_extract_chat_id`, `_extract_message_id`) are provided for use by other components that process raw Telegram updates.

## Database Tables

None. The Telegram module does not own any database tables (`migration_revisions()` returns `None`).

## Dependencies

None.

## Related Pages

- [Module System](module-system.md)
- [Approvals Module](approvals.md) -- telegram send tools should be listed in `gated_tools`
- [Pipeline Module](pipeline.md) -- telegram ingestion flows through the pipeline
- [Contacts Module](contacts.md) -- Telegram contact sync


### Approved raw-source fallback sequence

The authenticated Choice A decision for `bu-olvyv5` permits a long chunk's
fallback to use one ordered sequence of its exact original raw source. A
3999-unit fenced-code line can fit the visible bound while its opening and
closing fences push the raw span past that bound. Only a proven parse-entities
rejection permits splitting that raw span into requests of at most 4000 UTF-16
units; a fitting span uses one request. Every request requires its own exact
positive receipt, and failure retains only the accepted prefix without replay.
The chat lock, original deadline, shared rate-retry allowance, first accepted
reply reference and final keyboard cover the complete sequence. Plain requests
cannot fall back again, and another HTTP 400 cannot enter this path. Edits keep
their single bounded plain edit fallback and cannot append messages.

Credential-transition admission now requires a successful existing audit lookup.
An unavailable read is unknown and prevents sending; a genuinely absent marker,
a fresh independent transition and ordinary retryable failures retain their
existing behavior. The legacy diagnostic lookup helper remains compatible.
