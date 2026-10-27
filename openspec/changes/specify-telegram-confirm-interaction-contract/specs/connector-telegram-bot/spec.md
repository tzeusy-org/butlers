# Telegram Bot Connector — Delta

## ADDED Requirements

### Requirement: Generic Confirm Callback Ingestion

The connector's `callback_query` handling SHALL additionally recognize a `callback_data`
carrying the `cfm1:` prefix (`cfm1:<confirm_id>:<opt_idx>:<hmac>`) as a generic confirm
resolution event, per the `notify-confirm-interaction` capability, and route it to the
confirm-resolution path instead of the connector's default drop behavior. This routing is
strictly additive to the connector's existing recognition of `apr1:` (approval decisions)
and `cgi:` (gap-interview) prefixes: a `callback_data` matches at most one of the three
known prefixes, and any `callback_data` matching none of them retains the connector's
existing silent-drop behavior unchanged. The confirm-resolution path MUST NOT call the
approvals decision surface or any gap-interview route.

#### Scenario: Confirm callback routed additively

- **WHEN** a `callback_query` arrives whose `callback_data` begins with the `cfm1:` prefix
- **THEN** the connector routes it to the confirm-resolution path (verifying recipient-chat
  identity and HMAC per `notify-confirm-interaction`, then performing the atomic
  `pending_confirms` transition and reply-to-origin reentry), acknowledging the tap via
  `answerCallbackQuery`
- **AND** this routing does not alter the connector's existing handling of `apr1:` or
  `cgi:` prefixed `callback_data`, or its default-drop behavior for every other
  `callback_query`

#### Scenario: Non-confirm callback prefixes are unaffected

- **WHEN** a `callback_query` arrives whose `callback_data` begins with `apr1:` or `cgi:`,
  or matches none of the three known prefixes
- **THEN** it is handled exactly per its own existing requirement (or the existing
  default-drop behavior), with no interaction with the `cfm1:` confirm-resolution path

#### Scenario: Confirm resolution failure degrades gracefully

- **WHEN** the confirm-resolution path is invoked but the backing API or database is
  unreachable
- **THEN** the tap SHALL still be acknowledged via `answerCallbackQuery` with a graceful
  toast rather than left with a loading spinner, mirroring the existing gap-interview
  unreachable-API scenario
