# Email Module

> **Purpose:** IMAP/SMTP email integration providing inbox search, message reading, and optional send/reply tools.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Email module provides MCP tools for email operations via standard IMAP (inbox access) and SMTP (sending). It supports two credential scopes -- **user** (the owner's personal email) and **bot** (the butler's service email) -- with credentials resolved from the database at startup.

Email ingestion (receiving and processing inbound emails) is handled separately by `GmailConnector` via the connector-based pipeline. This module is focused on providing tools for butlers to search, read, and optionally send email.

**Important safety design**: Send/reply tools are only registered when `send_tools = true` in the module config. This ensures that only the Messenger butler (which has approval gates) can send outbound emails directly. All other butlers use the `notify()` core function for outbound delivery, which routes through the Messenger.

Source: `src/butlers/modules/email.py`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.email]
smtp_host = "smtp.gmail.com"
smtp_port = 587
imap_host = "imap.gmail.com"
imap_port = 993
use_tls = true
send_tools = false              # Only enable on Messenger butler

[modules.email.user]
enabled = false
address_env = "USER_EMAIL_ADDRESS"
password_env = "USER_EMAIL_PASSWORD"

[modules.email.bot]
enabled = true
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"
```

### Credential Resolution

- **User scope**: Resolved exclusively from the owner entity's `shared.entity_info` entries (types `email` and `email_password`). No environment variable fallback.
- **Bot scope**: Resolved via `CredentialStore` (DB-first with env fallback).

All credentials are pre-resolved at startup and cached in memory so synchronous IMAP/SMTP helpers can use them without async overhead.

## Tools Provided

| Tool | Condition | Description |
|------|-----------|-------------|
| `email_search_inbox` | Always | Search inbox via IMAP SEARCH. Returns up to 50 most recent matching message headers. |
| `email_read_message` | Always | Read a specific email by message ID. Returns headers and extracted plain-text body. |
| `email_send_message` | `send_tools = true` | Send an email via SMTP. |
| `email_reply_to_thread` | `send_tools = true` | Reply to an email thread with a thread ID reference. |

## Implementation Details

IMAP and SMTP operations use Python's stdlib `imaplib` and `smtplib` respectively, wrapped in `asyncio.to_thread()` to avoid blocking the event loop. The module creates fresh IMAP/SMTP connections per operation (no persistent connection pooling).

For multipart emails, `email_read_message` extracts the first `text/plain` part. HTML-only emails are not currently converted.

## Database Tables

None. The email module does not own any database tables (`migration_revisions()` returns `None`).

## Dependencies

None.

## Related Pages

- [Module System](module-system.md)
- [Approvals Module](approvals.md) -- email send tools should be listed in `gated_tools`
- [Pipeline Module](pipeline.md) -- email ingestion flows through the pipeline
