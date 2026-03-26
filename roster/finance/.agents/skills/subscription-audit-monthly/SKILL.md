---
name: subscription-audit-monthly
description: Monthly scheduled task that audits active subscriptions, identifies unused services, detects price changes, and alerts the user to cancel or keep
version: 1.0.0
trigger_patterns:
  - scheduled task subscription-audit-monthly
---

# Subscription Audit Monthly Skill

This skill provides a monthly scheduled task that reviews all active subscriptions, identifies which ones are active, unused, or have changed in price, and recommends cancellations to reduce unnecessary spending.

## Purpose

Help the user maintain a clean subscription inventory, detect unused services, spot price increases, and reduce recurring spending waste.

## Prerequisites

Before running the monthly subscription audit:
1. Subscriptions are tracked via `track_subscription()` with service name, amount, frequency, and renewal date
2. Baseline spending profiles include subscription data (from prior months)
3. Optional: User has configured audit preferences (which services to auto-audit, ignore list, etc.)
4. Telegram notifications are enabled

## Audit Flow

### 1. Retrieve Active Subscriptions

Use `subscription_audit()` to get:
- All active and paused subscriptions
- Amount, frequency, renewal date, auto-renew status
- Last known transaction for each service
- Days until next renewal

### 2. Classify Subscriptions by Usage

For each subscription, determine usage status:
- **Active**: Regular usage detected in past 30 days (merchant in recent transactions)
- **Paused**: Explicitly marked as paused by user
- **Unused**: No transactions detected in past 60+ days
- **Price Changed**: Amount differs from prior month
- **Due Soon**: Renewal within 7 days

### 3. Generate Audit Report

Compose a Telegram message organized by status:

```
📋 Monthly Subscription Audit

🟢 ACTIVE & HEALTHY (4 subscriptions):
• Netflix: $15.49/month (renews Apr 2)
  └ Active — watched shows last week
• Spotify: $9.99/month (renews Apr 3)
  └ Active — listened to playlists this week
• Gym: $35/month (renews Mar 28)
  └ Active — 6 visits this month
• Dropbox: $9.99/month (renews Apr 5)
  └ Active — file syncing enabled

🟡 PAUSED (1 subscription):
• Coursera: $39/month (paused by user)
  └ Last active Jan 2026

🔴 LIKELY UNUSED (2 subscriptions):
• Adobe Creative Cloud: $54.99/month (renews Mar 28)
  └ No activity in 90+ days — Consider cancelling to save $55/month
• SecureVPN: $11.99/month (renews Mar 31)
  └ No activity in 45+ days — Consider cancelling to save $12/month

⚠️ PRICE CHANGES (1 subscription):
• Disney+: increased from $10.99 to $13.99/month
  └ 27% price increase last month
  └ Continue or cancel?

**MONTHLY TOTAL**: $181.45 active + $39 paused = $220.45
**SAVINGS POTENTIAL**: $66.98/month if Adobe + SecureVPN cancelled

Would you like to:
1. Cancel unused subscriptions (Adobe, SecureVPN)
2. Review specific services
3. Keep everything as is
4. Adjust frequency of audits
```

### 4. Per-Subscription Recommendations

For each unused or price-changed subscription:

**Unused (Adobe Cloud, 90+ days):**
- Reason: No transactions from this service
- Recommendation: Likely not using — cancel to save $55/month
- Action: "Cancel subscription?" / "Keep for now"

**Price Changed (Disney+):**
- Old price: $10.99
- New price: $13.99
- Change: +$3/month
- Recommendation: "Significant price increase. Worth keeping?"
- Action: "Keep for now" / "Cancel due to price"

**Paused (Coursera):**
- Status: Explicitly paused
- Recommendation: No action needed unless user wants to cancel completely
- Action: "Resume?" / "Cancel permanently"

### 5. User Notification

Call `notify(channel="telegram", intent="send", message=<audit_summary>)` with the full report.

### 6. Memory Storage

Store audit findings:
```python
memory_store_fact(
    subject="finance_butler",
    predicate="subscription_audit_date",
    content="2026-03-26 — last audit performed",
    permanence="standard",
    importance=7.0,
    tags=["subscription", "audit"]
)

memory_store_fact(
    subject="Adobe Creative Cloud",
    predicate="usage_status",
    content="unused for 90+ days — candidate for cancellation",
    permanence="volatile",
    importance=7.0,
    tags=["subscription", "unused"]
)
```

## Scheduled Task Configuration

This skill runs as a monthly scheduled task (typically 1st of month at 10am):
- **Cron**: `0 10 1 * *` (1st of each month at 10am) or custom per user preference
- **Dispatch mode**: `prompt`
- **Output**: Notification via Telegram with full audit summary

## Error Handling

- If no subscriptions exist: "No subscriptions tracked. Start with `track_subscription()` to begin tracking."
- If audit data is unavailable: "Could not retrieve subscription data. Try again later."
- If price calculation fails: Log error, exclude that subscription from price-change reporting

## Example Scenarios

### Example 1: Clean Subscription List

```
4 active subscriptions, all used in past 30 days:
- Netflix (active)
- Spotify (active)
- Gym (active)
- Dropbox (active)

Result: ✅ All subscriptions active. No action needed.
```

### Example 2: Unused Service Detected

```
Adobe Creative Cloud subscription:
- Amount: $54.99/month
- Last transaction: 90 days ago
- Current status: Active (auto-renewing)

Recommendation: Cancel to save $55/month if not planning to use.
```

### Example 3: Price Increase

```
Disney+ subscription:
- Previous amount: $10.99/month
- New amount: $13.99/month
- Change: +$3/month (+27%)

Recommendation: Significant increase. Worth keeping?
```

### Example 4: Multi-Service Cleanup

```
Unused subscriptions detected (potential $120/month savings):
- Adobe: $55/month (90+ days unused)
- SecureVPN: $12/month (60+ days unused)
- Coursera: $39/month (paused, no activity)

If cancelled: Monthly spending reduces from $180 to $60 for subscriptions.
```

## Version History

- v1.0.0 (2026-03-26): Initial stub for monthly subscription audit task
