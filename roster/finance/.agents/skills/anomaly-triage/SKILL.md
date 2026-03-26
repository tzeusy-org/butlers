---
name: anomaly-triage
description: Interactive workflow for reviewing, investigating, and resolving spending anomalies
version: 1.0.0
---

# Anomaly Triage Skill

This skill provides a structured, interactive workflow for reviewing detected spending anomalies,
investigating their root cause, and resolving them — either by marking them as expected, flagging
them for follow-up, or identifying fraudulent charges.

## Purpose

Help the owner quickly triage unusual spending activity detected by the anomaly engine. The goal
is to close open anomalies with appropriate context: is this expected? Is this a problem? Does it
require immediate action? The workflow reduces noise and surfaces the anomalies that actually matter.

## When to Use

Use this skill when:
- The owner asks "what anomalies were detected?" or "show me unusual spending"
- The owner wants to review the anomaly digest
- The anomaly-digest scheduled task has fired and the owner wants to act on it interactively
- A specific transaction looks suspicious and needs investigation

## Prerequisites

Before starting the triage, gather context:

1. Run anomaly scan: `anomaly_scan(days_back=7, sensitivity="medium")` to surface recent anomalies
2. Optionally check baselines: the scan result includes why each transaction was flagged

## Triage Flow

Follow this structured flow. Work through anomalies in order of severity (highest first).

---

### Step 1: Fetch and Present Anomalies

```python
anomalies = anomaly_scan(days_back=7, sensitivity="medium")
```

If the result is empty or `status="insufficient_data"`, say: "No anomalies detected in the
past 7 days. Your spending looks normal." and exit.

Present anomalies sorted by severity descending:

```
Anomaly Review — past 7 days ([N] flagged)

🔴 HIGH SEVERITY ([N])
1. [Merchant]: $[amount] on [date]
   Type: [anomaly_type]
   Why flagged: [explanation]

🟠 MEDIUM SEVERITY ([N])
2. [Merchant]: $[amount] on [date]
   Type: [anomaly_type]
   Why flagged: [explanation]

🟡 LOW SEVERITY ([N])
3. [Merchant]: $[amount] on [date]
   Type: [anomaly_type]
   Why flagged: [explanation]
```

**Anomaly types:**
- `amount_spike`: Transaction amount significantly higher than merchant/category baseline
- `new_merchant`: Charge from a merchant not seen in transaction history
- `velocity_spike`: Category spending rate is unusually high for this point in the cycle
- `duplicate_suspected`: Same merchant, same amount, on same or adjacent days

---

### Step 2: Interactive Triage — One Anomaly at a Time

Work through anomalies from highest severity first. For each anomaly:

```
Bot: "Let's look at [Merchant] — $[amount] on [date]. [Why flagged explanation].

What would you like to do?"

Options:
- ✅ Expected — Mark as normal (e.g., planned purchase, annual fee)
- 🔍 Investigate — Show recent transactions for this merchant
- 🚨 Dispute — Flag as potentially fraudulent
- ⏭️ Skip — Move on without acting
```

#### Resolution: Expected

If the owner confirms the transaction is expected:

```python
memory_store_fact(
    subject=<merchant_name>,
    predicate="expected_anomaly",
    content="$[amount] on [date] — owner confirmed expected: [reason if provided]",
    permanence="volatile",
    importance=4.0,
    tags=["anomaly", "resolved", "expected"]
)
```

Confirm: "Got it. Marked as expected."

Note: If this is a recurring pattern (e.g., annual subscription charge), offer to update the
baseline by tracking it as a subscription: "Is this an annual charge? Should I track it as a
subscription so it doesn't flag again next year?"

#### Resolution: Investigate

Show recent transaction history for this merchant:

```python
list_transactions(merchant=<merchant_name>, limit=10)
```

Present the last 10 transactions for context:

```
Recent transactions — [Merchant]:
1. $[amount] on [date] — [category]
2. $[amount] on [date] — [category]
...
```

After showing history, re-prompt for a decision (Expected / Dispute / Skip).

#### Resolution: Dispute

If the owner flags the transaction as potentially fraudulent:

```python
memory_store_fact(
    subject=<merchant_name>,
    predicate="disputed_charge",
    content="$[amount] on [date] — owner flagged as potentially fraudulent. Source: [transaction_id if available]",
    permanence="stable",
    importance=9.0,
    tags=["anomaly", "dispute", "fraud-suspected"]
)
```

Confirm and surface action guidance:

```
🚨 Charge flagged: [Merchant] $[amount] on [date].

Recommended next steps:
1. Check your bank or card issuer's app for the charge details
2. Contact your card issuer to dispute the charge if unauthorized
3. Ask your bank to block or replace the card if multiple unauthorized charges exist

I've recorded this for your reference.
```

**Important scope note:** Do not initiate card blocks, disputes, or contact banks on behalf of
the owner. Surface the information and recommend external action.

#### Resolution: Skip

Note the skip without storing anything. Move to the next anomaly.

---

### Step 3: Triage Summary

After all anomalies are reviewed (or the owner says they're done), present a summary:

```
Anomaly Triage Complete

Reviewed: [N] anomalies
- ✅ Marked expected: [N]
- 🚨 Disputes flagged: [N]
- ⏭️ Skipped: [N]

[If disputes:] Remember to contact your card issuer about the flagged charge(s).
[If expected annual charges:] Consider tracking them as subscriptions to avoid future alerts.
```

---

### Step 4: Sensitivity Adjustment (Optional)

If the owner felt that too many or too few anomalies were flagged, offer to adjust sensitivity:

```
Bot: "The scan ran at 'medium' sensitivity. Would you like to adjust it?
- Low: Only flag extreme outliers
- Medium: Current setting (balanced)
- High: Flag anything slightly unusual"
```

Store the preference if the owner wants to change it:

```python
memory_store_fact(
    subject="user",
    predicate="anomaly_sensitivity_preference",
    content="prefers [low/medium/high] sensitivity for anomaly detection",
    permanence="stable",
    importance=6.0,
    tags=["anomaly", "preference", "sensitivity"]
)
```

---

## Adaptive Tips

**For quick daily triage (after anomaly-digest alert):**
- Focus on high-severity anomalies only
- Keep interactions brief: present the anomaly and ask Expected / Dispute / Skip
- 2-3 minutes total

**For weekly review:**
- Review all anomalies from the past 7 days
- Include medium and low severity
- Look for patterns across anomalies (multiple new merchants could signal compromised card)
- 5-10 minutes

**For deep investigation:**
- Focus on a single merchant or category
- Pull full transaction history with `list_transactions`
- Check for duplicate patterns with `detect_duplicates(days_back=30)`
- 10-15 minutes

---

## Error Handling

- If `anomaly_scan` returns `status="insufficient_data"`: "Not enough transaction history for
  anomaly detection. Check back after more transactions are recorded."
- If `anomaly_scan` returns no anomalies: "No anomalies detected. Your spending looks normal."
- If `list_transactions` returns no history for a merchant: "No prior history found for
  [Merchant] — this is the first recorded charge."
- If the owner disputes a charge but no transaction ID is available: store the merchant name,
  amount, and date; note that the transaction ID was unavailable

---

## Important Reminders

1. **Severity first** — always present high-severity anomalies before medium or low
2. **Brief explanations** — one sentence on why each transaction was flagged
3. **Neutral tone** — anomalies are not accusations; present them as "unusual, let's verify"
4. **Never initiate payment actions** — disputes and card replacements require the owner to act
5. **Store resolutions** — marking expected patterns in memory improves future signal quality
6. **Suggest subscription tracking** — recurring charges that cause repeat anomalies should be
   tracked to suppress future alerts

---

## Version History

- v1.0.0 (2026-03-26): Initial skill creation with triage flow, investigation, dispute handling,
  and sensitivity adjustment
