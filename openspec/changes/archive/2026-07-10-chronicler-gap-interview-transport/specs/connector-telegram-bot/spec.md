# Telegram Bot Connector — Spec delta for chronicler-gap-interview-transport

## MODIFIED Requirements

### Requirement: Update Type Handling
The connector SHALL process a defined subset of Telegram update types. It SHALL
also recognize one strictly additive `callback_query` exception: a
`callback_data` carrying the `cgi:` gap-interview prefix SHALL be routed to the
chronicler gap-interview resolve path instead of being dropped, while every
other `callback_query` SHALL retain its existing drop behavior.

#### Scenario: Processed update types
- **WHEN** a Telegram update arrives
- **THEN** the connector processes: `message`, `edited_message`, and `channel_post`

#### Scenario: Skipped update types
- **WHEN** a non-message update arrives (`callback_query`, `inline_query`, `chosen_inline_result`, etc.)
- **THEN** it is silently skipped — no ingest submission — UNLESS it is a
  `callback_query` whose `callback_data` carries the `cgi:` gap-interview
  prefix, which is handled per the next scenario

#### Scenario: Gap-interview one-tap callback routed
- **WHEN** a `callback_query` arrives whose `callback_data` begins with the
  `cgi:` prefix (`cgi:<interview_id>:<answer>`)
- **THEN** the connector SHALL route it to the chronicler gap-interview resolve
  path (`POST /api/chronicler/gap-interview/resolve`) instead of dropping it,
  and SHALL acknowledge the tap via `answerCallbackQuery` with an owner-facing
  toast
- **AND** this routing SHALL be strictly additive — any `callback_query` whose
  `callback_data` does not carry the `cgi:` prefix retains its existing drop
  behavior
- **AND** when the internal API URL is unconfigured or unreachable, the tap
  SHALL still be acknowledged with a graceful toast rather than left with a
  loading spinner

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0007 (Dashboard and API Surface)
- RFC 0014 (Chronicler Time Butler) §D7 API Surface
