---
name: anomaly-digest
description: Daily scheduled anomaly detection task that scans recent transactions and notifies the user of suspicious or unusual spending patterns
version: 1.0.0
trigger_patterns:
  - scheduled task anomaly-digest
---

# Anomaly Digest Skill

This skill provides a daily scheduled task that scans recent transactions for anomalies (unusual merchants, spending spikes, duplicate charges, etc.) and notifies the user via Telegram if significant issues are detected.

## Purpose

Monitor spending for anomalies that may indicate fraud, subscription duplicates, or unusual activity. Surface findings proactively so the user can take action immediately.

## Prerequisites

Before running the anomaly digest scan:
1. Transaction history is populated via `record_transaction()` or `bulk_record_transactions()`
2. Baseline spending profiles exist (created during initial onboarding or prior months)
3. User has configured anomaly detection sensitivity via preference (or use default threshold)
4. Telegram notifications are enabled for the user's contact

## Scan Flow

### 1. Anomaly Detection

Use `anomaly_scan(days_back=1)` to retrieve transactions from the past 24 hours and flag anomalies. The tool returns:
- New merchants not seen before
- Spending spikes (amount > baseline + 2 std dev)
- Duplicate charges (same merchant, same amount, within hours)
- High-risk category spikes (gambling, cash withdrawals)
- Subscription price changes

### 2. Anomaly Classification

Prioritize anomalies by severity:
- **Critical**: Duplicate charges, high-risk categories, fraud indicators
- **Warning**: Spending spikes > 50% above baseline, new high-amount merchants
- **Info**: New merchants below baseline, minor category shifts

### 3. User Notification

If anomalies are found:
1. Compose a concise Telegram message summarizing the anomalies
2. Group by severity level
3. Provide one action per anomaly (e.g., "Mark as duplicate", "Report as fraud", "Investigate this merchant")

Example message:
```
🔍 Spending Anomalies Detected (Last 24h)

🚨 CRITICAL:
• Duplicate charge detected: DoorDash $42.50 (2 charges within 1 hour)
  → Mark as duplicate and request refund?

⚠️ WARNING:
• Unusual merchant: "CRYPTO_EXCHANGE" $500
  → Not in your typical spending pattern
• Spending spike: Dining $127.50 (usual: $45/day)
  → 3x your daily average

ℹ️ INFO:
• New merchant: "FARMER'S_MARKET_DOWNTOWN" $34.20
  → First time here, normal amount

Reply with the action number or "review all" to investigate further.
```

4. Call `notify(channel="telegram", intent="send", message=<summary>)` to deliver the notification

### 4. No Anomalies Path

If no anomalies are detected, exit silently (no notification needed):
```
No anomalies detected in the past 24 hours. All transactions appear normal.
```

## Memory Integration

After anomaly detection, optionally store findings:
- Store flagged merchants for future baseline updates
- Record false positives to refine detection rules
- Update anomaly sensitivity preferences if user adjusts thresholds

## Scheduled Task Configuration

This skill runs as a scheduled task (typically daily at 9pm or user-configured time):
- **Cron**: `0 21 * * *` (daily at 9pm) or custom per user preference
- **Dispatch mode**: `prompt`
- **Output**: Notification via Telegram if anomalies found

## Error Handling

- If `anomaly_scan()` returns an error or empty baseline: Skip notification, log issue
- If Telegram is unavailable: Log error and attempt retry on next run
- If too many anomalies detected (>20): Summarize top 5 by severity, offer "view all" option

## Example Scenarios

### Example 1: Duplicate Charge Detected

```
Transaction 1: "NETFLIX" $15.49 at 2026-03-26 08:30
Transaction 2: "NETFLIX" $15.49 at 2026-03-26 08:35

Anomaly: Duplicate charge within 5 minutes
Action: Mark as duplicate, request refund, contact Netflix support
```

### Example 2: Spending Spike

```
Baseline dining spend: $45/day
Today's dining spend: $127.50

Anomaly: 183% above daily average
Action: Investigate, possibly legitimate (group dinner), or flag category for further monitoring
```

### Example 3: New High-Risk Merchant

```
Merchant: "ADULT_CONTENT_PROVIDER"
Amount: $49.99
Baseline: $0 (new merchant, high-risk category)

Anomaly: New merchant in high-risk category
Action: Verify legitimacy, mark as known merchant or dispute
```

### Example 4: No Anomalies

```
24-hour transaction summary: 5 transactions, all normal merchants and amounts
Baseline comparison: All within expected ranges
Result: No notification sent, user has clean spending day
```

## Version History

- v1.0.0 (2026-03-26): Initial stub for daily anomaly detection task
