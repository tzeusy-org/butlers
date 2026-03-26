---
name: subscription-renewal-alerts
description: Scheduled task skill — weekly subscription renewal scan with price change detection, alerts via notify intent=send
version: 1.1.0
trigger_patterns:
  - scheduled task subscription-renewal-alerts
---

# Skill: Subscription Renewal Alerts

## Purpose

Weekly scheduled scan for subscriptions renewing within the next 7 days, plus detection of
price changes in recent subscription charges. For each upcoming renewal, surface the service
name, amount, renewal date, and auto-renew status. Flag any subscriptions where the charged
amount has changed vs the tracked amount. Deliver a Telegram alert so the owner can cancel or
adjust before being charged. If no renewals are approaching and no price changes detected,
send nothing.

## When to Use

Use this skill when:
- The `subscription-renewal-alerts` scheduled task fires (cron: `20 21 * * 0`, weekly on Sunday at 21:20)

## Execution Protocol

### Step 1: Fetch Active Subscriptions and Price Changes

Run both calls:

1. Query active subscriptions renewing within 7 days. Use whatever subscription listing tool
   is available (e.g., a future `list_subscriptions` tool), or fall back to
   `memory_search(query="active subscriptions")` if the tool is not yet available.

   **Preferred (when tool is available):**
   ```python
   list_subscriptions(status="active", renewing_within_days=7)
   ```

   **Fallback:**
   ```python
   memory_search(query="active subscriptions renewing soon")
   ```

2. Check for price changes in recent subscription charges:

   ```python
   price_changes = detect_price_changes(days_back=60)
   ```

   This returns subscriptions where the most recent charge amount differs from the tracked
   subscription amount. Each result includes: `service`, `tracked_amount`, `recent_amount`,
   `change_pct`, and `last_charge_date`.

### Step 2: Early Exit — Nothing to Report

If no active subscriptions renew within 7 days **and** `detect_price_changes` returns no
results, **do not send a notification**. Exit immediately.

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

### Step 4: Classify Price Changes

For each price change returned by `detect_price_changes`:
- Determine direction: price increase or price decrease
- Compute delta: `recent_amount - tracked_amount`
- Note whether the service is also renewing soon (overlap with Step 3 results)

Sort by absolute `change_pct` descending (largest change first).

### Step 5: Compose Alert

Format concisely for Telegram. Include both sections if data exists for both; omit any section
with no data:

```
Subscription update — [date]

🔴 RENEWING SOON (auto-renews):
- [Service]: $[amount] — renews [date] ([N] days) ⚡ auto-renew ON

🟡 UPCOMING RENEWALS:
- [Service]: $[amount] — renews [date] ([N] days) [auto-renew: ON/OFF]
- [Service]: $[amount] — renews [date] ([N] days) [auto-renew: OFF — manual action needed]

Total charges expected: $[sum]

💲 PRICE CHANGES DETECTED:
- [Service]: $[tracked_amount] → $[recent_amount] ([+/-]N%) — last charged [date]
```

Omit the "RENEWING SOON" section if no auto-renewing subscriptions are within 3 days.
Omit the "PRICE CHANGES DETECTED" section if `detect_price_changes` returns no results.

For subscriptions with `auto_renew=false`, note "manual action needed" to remind the owner to
initiate the payment or renewal themselves.

### Step 6: Deliver Notification

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
- `detect_price_changes(days_back=60)` called to surface any price changes
- If no upcoming renewals and no price changes: session exits without sending
- If renewals or price changes exist: alert sent via `notify(intent="send")` with service names,
  amounts, dates, auto-renew status, and price change details
- Session exits after delivery — no interactive follow-up in this session
