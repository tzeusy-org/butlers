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

- **User scope**: Token resolved from owner entity's `shared.entity_info` (type `telegram_bot_token`).
- **Bot scope**: Token resolved via `CredentialStore` (DB-first with env fallback).

### Webhook Setup

When `webhook_url` is configured, the module calls the Telegram `setWebhook` API at startup. When omitted, no webhook is set (suitable for polling-based setups).

## Tools Provided

| Tool | Description |
|------|-------------|
| `telegram_send_message` | Send a message to a chat by chat ID |
| `telegram_reply_to_message` | Reply to a specific message by chat ID and message ID |
| `telegram_react_to_message` | React to a message with an arbitrary emoji |

## Markdown Conversion

Outbound message text is converted from Markdown to Telegram-compatible HTML before sending. The converter handles:

- `**bold**` -> `<b>bold</b>`
- `*italic*` -> `<i>italic</i>`
- `` `code` `` -> `<code>code</code>`
- ` ```code blocks``` ` -> `<pre>code blocks</pre>`
- `~~strikethrough~~` -> `<s>strikethrough</s>`

HTML parse mode is used instead of MarkdownV2 because HTML only requires escaping `<`, `>`, `&`, whereas MarkdownV2 demands backslash-escaping of many special characters, making LLM-generated text fragile.

## Lifecycle Reactions

The module defines three reaction constants used by the daemon's ingest pipeline:

| Constant | Emoji | When Used |
|----------|-------|-----------|
| `REACTION_IN_PROGRESS` | Eyes | Message received, processing started |
| `REACTION_SUCCESS` | Thumbs up | Pipeline completed successfully |
| `REACTION_FAILURE` | Space invader | Pipeline error |

The `react_for_ingest()` method parses `external_thread_id` (format: `"<chat_id>:<message_id>"`) from the ingest envelope and sets the appropriate reaction. Failures are silently logged -- reaction errors never block message processing.

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
- [Contacts Module](contacts.md) -- Telegram contact sync and chat ID enrichment
