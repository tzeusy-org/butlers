# Telegram Bot Connector — Delta

## ADDED Requirements

### Requirement: Generic Confirm Callback Ingestion

ID: REQ-connector-telegram-bot-003
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

The connector's `callback_query` handling SHALL additionally recognize a `callback_data`
carrying the `cfm1:` prefix (`cfm1:<confirm_id>:<opt_idx>:<hmac>`) as a generic confirm
resolution event, per the `notify-confirm-interaction` capability, and route it to the
confirm-resolution path instead of the connector's default drop behavior. This routing is
strictly additive to the connector's existing recognition of `apr1:` (approval decisions)
and `cgi:` (gap-interview) prefixes: a `callback_data` matches at most one of the three
known prefixes, and any `callback_data` matching none of them retains the connector's
existing silent-drop behavior unchanged. The connector MUST NOT call a domain sink directly.
It authenticates and forwards typed
callbacks to the confirm service using a credential restricted to the exact confirm routes.
The confirm service dispatches only registered sinks and cannot invoke approval decisions.
Runtime emission remains dark until the canonical RFC 0038 switch; disabled confirms fail
explicitly rather than producing buttons that cannot resolve.

#### Scenario: Confirm callback routed additively

- **WHEN** a `callback_query` arrives whose `callback_data` begins with the `cfm1:` prefix
- **THEN** the connector verifies the token and forwards the authenticated sender/chat
  through the exact confirm-service API route, acknowledging the tap via `answerCallbackQuery`
- **AND** the service verifies stored recipient identity and atomically records the answer
  and its durable admission or sink-dispatch obligation
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
