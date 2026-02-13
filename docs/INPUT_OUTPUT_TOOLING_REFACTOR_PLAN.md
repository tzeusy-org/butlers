# Input/Output Tooling Refactor Plan

## Goal

Introduce explicit module-level I/O concepts so tools are separated by:

- direction: input vs output
- identity: user-owned account vs butler-owned bot account
- approval model: auto, conditional, or always-approved

This plan adds first-class `user_inputs` and `user_outputs` concepts to modules, and standardizes tool naming so user-identity and bot-identity operations are never mixed.

---

## Problem Statement

Current module tools flatten materially different behaviors into one surface.

- `telegram` historically exposed unprefixed send/update tools without identity separation.
- `email` historically exposed unprefixed send/search/read tools, but did not distinguish:
  - butler mailbox flow (dedicated butler address)
  - user mailbox flow (acting on user-owned inbox/sent identity)
- approval policy is harder to reason about when tool names do not encode identity and risk.

Result: higher ambiguity, weaker security boundaries, and harder policy enforcement.

---

## Design Principles

1. Identity-explicit tooling
- Every tool name must declare whether it acts as `user_*` or `bot_*`.

2. Direction-explicit tooling
- Inputs and outputs are modeled separately in module metadata and docs.

3. Approval-by-default safety
- `user_*` outputs default to approval required unless explicitly whitelisted by standing rules.
- `bot_*` outputs can default to auto/conditional based on module policy.

4. Strict greenfield consistency
- No backward-compatibility aliases, wrappers, or legacy naming exceptions.
- All signature/name violations must be fixed in the same refactor PR.

---

## New Module Contract

### Module Metadata Additions

Add explicit I/O declarations to module definitions:

- `user_inputs() -> list[IOToolSpec]`
- `user_outputs() -> list[IOToolSpec]`
- `bot_inputs() -> list[IOToolSpec]` (optional but recommended)
- `bot_outputs() -> list[IOToolSpec]` (optional but recommended)

`IOToolSpec` should include:

- `tool_name`
- `channel` (telegram, email, etc.)
- `direction` (`input` | `output`)
- `identity` (`user` | `bot`)
- `approval_default` (`none` | `conditional` | `always`)
- `description`

`tool_metadata()` should remain the hook for argument sensitivity, but now backed by clearer I/O identity declarations.

---

## Naming Convention

Required naming format:

- `user_<channel>_<action>`
- `bot_<channel>_<action>`

Examples:

- `user_telegram_send_message`
- `bot_telegram_send_message`
- `user_email_search_inbox`
- `bot_email_send_message`

Non-prefixed names are signature/name violations and must be removed, not deprecated.

## Signature/Name Compliance Requirements

This is a hard requirement for this refactor:

- No legacy tool names remain registered.
- No callsites reference legacy names.
- No prompt templates, routing code, or tests reference legacy names.
- No config/docs examples use legacy names.

Known violations to fix as part of this refactor:

- Telegram: unprefixed send/update tool names
- Email: unprefixed send/search/read/check-and-route tool names

---

## Telegram Refactor

## Inputs

1. User-account ingestion (full visibility model)
- Source: Telegram client session tied to the user account.
- Tools:
  - `user_telegram_list_dialogs`
  - `user_telegram_get_updates`
  - `user_telegram_get_message_history`
- Purpose: ingest conversations the user sees across their account.

2. Butler-bot ingestion (bot DM / command model)
- Source: Telegram bot webhook/polling.
- Tools:
  - `bot_telegram_get_updates`
  - `bot_telegram_get_chat_messages`
- Purpose: ingest messages sent to the butler bot identity.

## Outputs

1. Bot replies (lower risk, butler identity)
- Tools:
  - `bot_telegram_send_message`
  - `bot_telegram_reply_to_message`
- Approval default: `none` or `conditional` (policy-driven).

2. Act-as-user replies (high risk, user identity)
- Tools:
  - `user_telegram_send_message`
  - `user_telegram_reply_to_message`
- Approval default: `always`.

---

## Email Refactor

## Inputs

1. Butler mailbox intake
- Source: dedicated butler inbox (for example support-like or agent inbox).
- Tools:
  - `bot_email_search_inbox`
  - `bot_email_read_message`
  - `bot_email_check_and_route_inbox`

2. User mailbox intake
- Source: user-owned mailbox via user OAuth credentials.
- Tools:
  - `user_email_search_inbox`
  - `user_email_read_message`
  - `user_email_list_threads`

## Outputs

1. Butler mailbox send
- Tools:
  - `bot_email_send_message`
  - `bot_email_reply_to_thread`
- Approval default: `none` or `conditional`.

2. User mailbox send (send-as user)
- Tools:
  - `user_email_send_message`
  - `user_email_reply_to_thread`
  - `user_email_draft_reply` (optional pre-send pattern)
- Approval default: `always` for send actions.

---

## Approval Model Integration

Update approvals configuration and defaults to align with prefixed tool names.

Baseline policy:

- `user_*_*send*` and `user_*_*reply*` => gated by default
- `bot_*` outputs => configurable per butler
- all inputs => normally ungated, but sensitive read scopes can be flagged in `tool_metadata()`

Standing approval rules remain supported, but rules become safer and clearer because tool identity is explicit.

---

## Configuration Model Changes

Module config should split credentials and scopes per identity.

Example sketch:

```toml
[modules.telegram.user]
provider = "telegram_client"
credentials_env = "USER_TELEGRAM_SESSION"
enabled = true

[modules.telegram.bot]
provider = "telegram_bot_api"
token_env = "BUTLER_TELEGRAM_TOKEN"
enabled = true

[modules.email.user]
provider = "gmail_oauth"
credentials_env = "USER_EMAIL_OAUTH_JSON"
enabled = true

[modules.email.bot]
provider = "smtp_imap"
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"
enabled = true
```

---

## Migration Plan

## Phase 1: Contract + Metadata

- Add I/O descriptor types and module interface methods.
- Update approvals/sensitivity plumbing to consume identity-aware metadata.
- Enforce naming validation that rejects non-prefixed tool names.

## Phase 2: Telegram Split

- Introduce prefixed Telegram tools (`user_telegram_*`, `bot_telegram_*`).
- Rename all Telegram callsites and tests in the same phase.
- Add approvals defaults for new user-output tools.

## Phase 3: Email Split

- Introduce prefixed Email tools (`user_email_*`, `bot_email_*`).
- Rename all Email callsites and tests in the same phase.
- Split credentials/config paths by identity.

## Phase 4: Routing + UX

- Update message pipeline and switchboard prompts to include identity-aware source metadata.
- Ensure user-visible explanations mention when approval is needed due to user-identity output.
- Ensure only prefixed tool names are routable.

---

## Testing Plan

1. Unit tests
- Tool registration: all expected prefixed tools are present.
- Module metadata: `user_inputs` / `user_outputs` correctly declared.
- Approval defaults: user-output tools are gated by default.

2. Integration tests
- Telegram:
  - bot message ingest + bot reply
  - user-account ingest + user send requiring approval
- Email:
  - bot mailbox read/send flow
  - user mailbox read/send flow with approval gate

3. Regression tests
- Verify no legacy tool names are registered.
- Verify all route/tool invocations use prefixed names only.
- Verify lint/static checks fail when non-prefixed names are introduced.

---

## Risks and Mitigations

1. Scope creep in provider implementations
- Mitigation: split interface first, keep provider coverage minimal in first pass.

2. Breaking existing automations
- Mitigation: treat this as greenfield; update all first-party callsites in one atomic refactor.

3. Approval misconfiguration
- Mitigation: ship safe defaults that gate all `user_*` output tools.

---

## Definition of Done

- Modules expose identity-prefixed input/output tools.
- `user_inputs` and `user_outputs` are first-class in module contract/metadata.
- Telegram and Email both support distinct user-vs-bot flows.
- Approval behavior differs by identity and is enforced by default.
- No legacy unprefixed tools or signatures remain in code, tests, prompts, or docs.
