---
name: subscription-renewal-alerts
description: Scheduled task skill — daily subscription renewal scan, alerts for renewals within 7 days via notify intent=send
version: 1.0.0
trigger_patterns:
  - scheduled task subscription-renewal-alerts
---

# Skill: Subscription Renewal Alerts

## Purpose

Daily scheduled scan for subscriptions renewing within the next 7 days. For each upcoming
renewal, surface the service name, amount, renewal date, and auto-renew status. Deliver a
Telegram alert so the owner can cancel or adjust before being charged. If no renewals are
approaching, send nothing.

## When to Use

Use this skill when:
- The `subscription-renewal-alerts` scheduled task fires (cron: `30 8 * * *`, daily at 08:30)

## Execution Protocol

### Step 1: Fetch Active Subscriptions

The finance tools do not have a dedicated "upcoming renewals" query. Use the available tools:

1. Query active subscriptions by filtering for `status="active"` and `next_renewal` within 7
   days. Use whatever subscription listing tool is available (e.g., a future `list_subscriptions`
   tool), or fall back to `memory_search(query="active subscriptions")` if the tool is not yet
   available.

   **Preferred (when tool is available):**
   ```python
   list_subscriptions(status="active", renewing_within_days=7)
   ```

   **Fallback:**
   ```python
   memory_search(query="active subscriptions renewing soon")
   ```

2. If no subscription tool or memory results exist, send nothing and exit.

### Step 2: Early Exit — No Upcoming Renewals

If no active subscriptions renew within 7 days, **do not send a notification**. Exit
immediately.

### Step 3: Classify Renewals

For each subscription renewing within 7 days, note:
- `service`: service name (e.g., "Netflix", "Spotify")
- `amount` + `currency`: renewal charge
- `next_renewal`: renewal date
- `auto_renew`: whether it renews automatically (true/false)
- Days until renewal: compute from `next_renewal` vs today

Sort by `next_renewal` ascending (soonest first).

Flag any subscription where `auto_renew=true` and renewal is within 3 days — these are highest
priority since the charge is imminent and unavoidable without action today.

### Step 4: Compose Alert

Format concisely for Telegram:

```
Subscription renewals — next 7 days

🔴 RENEWING SOON (auto-renews):
- [Service]: $[amount] — renews [date] ([N] days) ⚡ auto-renew ON

🟡 UPCOMING:
- [Service]: $[amount] — renews [date] ([N] days) [auto-renew: ON/OFF]
- [Service]: $[amount] — renews [date] ([N] days) [auto-renew: OFF — manual action needed]

Total charges expected: $[sum]
```

Omit the "RENEWING SOON" section if no auto-renewing subscriptions are within 3 days.

For subscriptions with `auto_renew=false`, note "manual action needed" to remind the owner to
initiate the payment or renewal themselves.

### Step 5: Deliver Notification

```python
notify(
    channel="telegram",
    intent="send",
    message=<formatted_alert>,
    request_context=<session_request_context>
)
```

Use `intent="send"` (not `reply`) — this is a proactive scheduled delivery.

## Exit Criteria

- Active subscriptions queried for renewals within 7 days
- If no upcoming renewals: session exits without sending
- If renewals exist: alert sent via `notify(intent="send")` with service names, amounts, dates,
  and auto-renew status
- Session exits after delivery — no interactive follow-up in this session
