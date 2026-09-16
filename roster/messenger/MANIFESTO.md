# Messenger: Infrastructure Contract

**Service type:** Staffer (infrastructure)
**Port:** 41104
**DB:** `butlers` / **Schema:** `messenger`

---

## Purpose

The Messenger is the sole owner of outbound user-channel delivery. It executes delivery intents routed from the Switchboard, turning `notify.v1` payloads into concrete sends and replies on Telegram, Email, WhatsApp, and explicitly targeted in-room voice. Voice is a physical side effect, not a best-effort notification: it remains unavailable unless the authenticated lineage, provider, DND, presence, and replay-fence contract in RFC 0034 is satisfied.

---

## Responsibilities

- **Outbound delivery ownership:** Execute all user-channel sends and replies. No other agent may call channel egress tools directly.
- **Channel tool surface:** Own and expose `telegram_send_message`, `telegram_reply_to_message`, `email_send_message`, `email_reply_to_thread`, `whatsapp_send_message`, `whatsapp_reply_to_message`. This is enforced as a configuration/policy requirement: non-messenger agents must not enable, register, or ship these outbound channel egress tools.
- **Delivery validation:** Validate `notify.v1` payloads before any side effect. Reject invalid or missing targeting fields with no delivery attempt.
- **Outcome reporting:** Return deterministic adapter outcome and error payloads.
- **Lineage preservation:** Retain `origin_butler` and `request_context` in all responses for audit trail.
- **Voice authority boundary:** Accept voice only from authenticated Switchboard
  lineage, resolve only versioned opaque Messenger endpoints, and orchestrate
  current DND/quiet-hours, fresh Home-owned room-presence attestation, provider
  admissibility, and at-most-once physical handoff before any playback.
- **Voice uncertainty truth:** Treat any handoff that may have started as
  non-retryable `ambiguous` or `failed`; retain a content-blind replay fence and
  request at most one separately keyed text-only non-voice fallback through Switchboard.

## Non-Responsibilities

- Messenger does **not** classify messages or perform routing decisions (delegated to Switchboard).
- Messenger does **not** contain domain logic or knowledge (delegated to domain butlers).
- Messenger does **not** initiate autonomous behavior or scheduled prompts.
- Messenger does **not** recursively call `notify()` for its own outbound sends.
- Messenger does **not** infer voice from a contact, entity, preference, room
  name, device id, schedule, insight, or omitted channel. Voice is reply or one
  explicit opaque endpoint send only.
- Messenger does **not** treat Live Listener VAD/ASR, recent speech, generic
  at-home context, or presence in another room as owner-positive room presence.
- Messenger does **not** bypass DND/quiet hours for priority or approval, defer
  or coalesce voice, retry after possible provider start, persist audio/raw
  presence/provider bodies, use cloud TTS, or restore generic delivery tracking.
- Messenger does **not** call Home directly or read Home's schema. Presence and
  any admitted Home/HA provider path are brokered through Switchboard under RFC
  0034 and RFC 0028.

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
| Rate limiting | Channel API returns rate-limit error | Return the provider outcome; caller recovery remains authoritative |
| Messenger unreachable | `notify()` from domain butlers times out | Escalate; domain butler delivery halts until Messenger restores |
| Payload validation failure | Missing or malformed `notify.v1` fields | Returns `validation_error` with no side effect; safe to retry after fixing payload |
| Voice lineage or binding invalid | Switchboard assertion cannot be verified, or opaque endpoint/binding version is absent/stale | Fail before presence/provider access; invalid lineage receives no fallback |
| Voice provider unavailable | No exact local-first provider profile is admissible | Do not call a provider; request at most one linked text-only non-voice fallback |
| Voice quiet or presence denied | DND/quiet is active, or room evidence is missing/stale/absent/unknown/conflicting | Suppress immediately; never queue voice for later; request at most one linked text-only non-voice fallback |
| Voice handoff uncertain | Provider may have crossed its start boundary but cannot confirm completion | Record `ambiguous`, never retry speech, and permit only the fixed uncertainty fallback |
| Voice provider rejected before start | Provider proves no playback began | Record `safe_retry`; never schedule a retry; a later explicit replay reruns every gate |

---

## Dependency Graph

### Depends On

- **Switchboard:** Routes `notify.v1` delivery intents from domain butlers to Messenger
- **Telegram Bot API:** External dependency for Telegram delivery
- **Email SMTP provider:** External dependency for email delivery
- **WhatsApp bridge:** External dependency for WhatsApp delivery
- **Switchboard voice control plane:** Authenticates voice origin/lineage and
  resolves at most one separately keyed text-only non-voice fallback
- **Home butler (via Switchboard only):** Owns fresh room-presence facts and any
  admitted RFC 0028 Home/HA provider action
- **Admissible local voice provider:** Exact versioned profile proving
  start/confirm/no-start/ambiguous semantics; otherwise voice stays unavailable
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
