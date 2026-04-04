# Messenger — Infrastructure Contract

**Service type:** Staffer (infrastructure)
**Port:** 41104
**Schema:** `messenger`

---

## Purpose

The Messenger is the sole owner of outbound user-channel delivery. It executes delivery intents routed from the Switchboard, turning `notify.v1` payloads into concrete sends and replies on Telegram and Email.

---

## Responsibilities

- **Outbound delivery ownership:** Execute all user-channel sends and replies. No other agent may call channel egress tools directly.
- **Channel tool surface:** Own and expose `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`. Non-messenger agents attempting to register these tools have them silently stripped at startup.
- **Delivery validation:** Validate `notify.v1` payloads before any side effect. Reject invalid or missing targeting fields with no delivery attempt.
- **Outcome reporting:** Return deterministic status and error payloads with delivery identifiers when available.
- **Lineage preservation:** Retain `origin_butler` and `request_context` in all responses for audit trail.

## Non-Responsibilities

- Messenger does **not** classify messages or perform routing decisions (delegated to Switchboard).
- Messenger does **not** contain domain logic or knowledge (delegated to domain butlers).
- Messenger does **not** initiate autonomous behavior or scheduled prompts.
- Messenger does **not** recursively call `notify()` for its own outbound sends.

---

## SLAs

| Metric | Target |
|---|---|
| Delivery latency (Telegram) | < 10 s p99 from intent receipt to send confirmation |
| Delivery latency (Email) | < 30 s p99 from intent receipt to SMTP acceptance |
| Availability | Must be running whenever any domain butler may produce outbound notifications |
| Concurrent delivery sessions | Up to 3 simultaneous |

---

## Failure Modes and Recovery

| Failure | Symptom | Recovery |
|---|---|---|
| Telegram channel unavailable | `telegram_send_message` returns `target_unavailable` | Caller retries via `notify()` after backoff; Messenger does not self-retry |
| Email SMTP failure | `email_send_message` returns `target_unavailable` or `timeout` | Caller retries; Messenger returns deterministic error class |
| Auth failure (bot token/password) | All sends fail with `internal_error` | Operator rotates credentials via dashboard secrets UI; Messenger picks up on next session |
| Rate limiting | Channel API returns rate-limit error | Returns `overload_rejected`; caller applies exponential backoff |
| Messenger unreachable | `notify()` from domain butlers times out | Escalate; domain butler delivery halts until Messenger restores |
| Payload validation failure | Missing or malformed `notify.v1` fields | Returns `validation_error` with no side effect; safe to retry after fixing payload |

---

## Dependency Graph

### Depends On

- **Switchboard:** Routes `notify.v1` delivery intents from domain butlers to Messenger
- **Telegram Bot API:** External dependency for Telegram delivery
- **Email SMTP provider:** External dependency for email delivery
- **PostgreSQL (`butlers.messenger` schema):** Session logging, state store

### Depends On Messenger

- **All domain butlers:** Use `notify()` to request outbound delivery; Messenger is the sole execution path
- **Switchboard:** Dispatches routed delivery intents to Messenger

---

## Capacity Limits

| Parameter | Value |
|---|---|
| Max concurrent runtime sessions | 3 |
| Approval expiry (default) | 48 hours |
| Approval risk tier (message sends) | medium |

---

## Escalation

If Messenger is unreachable, all outbound user-channel communication from domain butlers halts. Domain butlers accumulate unanswered `notify()` calls.

- Users receive no replies to their messages.
- Scheduled notifications are silently lost if not retried.

Escalate with severity HIGH if Messenger is down for more than 5 minutes. Escalate CRITICAL if the outage coincides with high-volume user traffic.
