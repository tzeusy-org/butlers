---
name: upcoming-bills-check
description: Scheduled task skill — weekly bills check with reconciliation sweep, then an action-first digest: needs-action items first, auto-pays/predictions as quiet context. Sent once via notify intent=send.
version: 2.0.0
trigger_patterns:
  - scheduled task upcoming-bills-check
---

# Skill: Upcoming Bills Check

## Purpose

Weekly bill digest that respects the owner's attention. **First**, run the reconciliation sweep
to auto-settle bills matched by existing transactions (the backstop). Then lead with the few bills
that genuinely **need action** (manual, confirmed, money owed). Auto-debited bills and
pattern-based predictions are shown only as quiet, no-action context — never as alarms. If nothing
needs action and there is no useful context, send nothing.

The failure mode this skill exists to prevent: a "🚨 OVERDUE" blast where 80% of the lines are
auto-paid, predicted, stale, or $0 placeholders. That trains the owner to ignore the digest.

## When to Use

Use this skill when the `upcoming-bills-check` scheduled task fires
(cron: `15 21 * * 0`, weekly on Sunday at 21:15).

## Execution Protocol

### Step 0: Run Reconciliation Sweep (FIRST)

Before fetching upcoming bills, run the deterministic reconciliation sweep. This is the weekly
**backstop** — it settles bills that have matching payment transactions but were missed by the
inline hook (e.g. a transaction recorded before the bill existed).

```python
reconciliation = reconcile_bills(lookback_days=90)
```

`reconcile_bills` returns:
- `auto_settled`: bills just settled in this sweep — each has `bill_id`, `payee`, `amount`,
  `paid_at`, `txn_id`
- `candidates`: ambiguous matches needing confirmation — each has `bill_id`, `payee`, `due_date`,
  `amount`, `candidates` (list of possible matching transactions)

Capture these results. **Do not surface `auto_settled` bills in the unpaid sections** —
they are now paid. Confirmed ambiguous candidates must still be reported so the owner can decide.

### Step 1: Fetch Bills and Predictions

Run both calls (these reflect the post-reconciliation state):

```python
# Tracked bills, pre-segmented by whether the owner must act.
bills = upcoming_bills(days_ahead=14, include_overdue=True)

# Pattern-based predictions from transaction history (read-only — see Step 4).
predictions = predict_bills(days_ahead=30)
```

`upcoming_bills` returns **three buckets** plus totals — the segmentation is already done for you,
do not re-derive it:

- `needs_action` — confirmed bills the owner must pay manually (not autopay, not predicted,
  amount > 0). Each item: `{bill, urgency, days_until_due}`, `urgency` ∈ `overdue` / `due_today` /
  `due_soon`.
- `autopay` — auto-debited bills (GIRO / CPF / card autopay). Informational only.
- `predicted` — pattern rows tracked as bills (should be rare). Informational only.
- `suppressed_placeholders` — count of $0 placeholders already hidden for you. Do **not** surface
  these; they are awaiting an amount and are not actionable.
- `totals.needs_action_amount` — the only figure that represents money the owner must actively move.

### Step 2: Early Exit — Nothing Worth Sending

Send **nothing** and exit immediately if ALL of these hold:

- `reconcile_bills` returned no `auto_settled` entries
- `reconcile_bills` returned no `candidates`
- `bills.needs_action` is empty
- `bills.autopay` is empty
- `predictions` has no entries with `is_tracked=false`

### Step 3: Compose the Digest (compose fully BEFORE notifying)

Build the **entire** message first. Do not call `notify` from intermediate data — see Step 5.

Format for mobile Telegram. Lead with reconciliation results (if any), then action items; demote
the rest:

```
Bills — [Date]

✅ Auto-settled ([N]) — matched and settled in this sweep
- [Payee]: [amount] — paid [paid_at date]

❓ Confirm needed ([N]) — ambiguous matches, please verify
- [Payee]: [bill_amount] due [due_date] — possible match: [txn_amount] at [merchant] on [posted_at]

⚠️ Needs action ([N]) — [currency] [needs_action_amount]
- [Payee]: [amount] — [overdue N days | due today | due in N days]

🔁 Auto-pays (no action)
- [Payee]: [amount] — auto-debits [date]

👀 Heads-up (predicted, not yet tracked)
- [Payee]: ~[predicted_amount] — expected [predicted_date]
```

Rules:
- **Only the "Needs action" section uses urgency framing.** Sort it by `urgency` (overdue → due
  today → due soon), then amount descending.
- Omit any section whose bucket is empty. Omit the "🚨"/alarm tone unless there is a genuinely
  overdue `needs_action` item.
- If `needs_action` is empty, open with `✅ Nothing needs action this week.` then show auto-pays /
  heads-up if present.
- The header total is `totals.needs_action_amount` only — never sum in autopay/predicted/placeholder
  amounts (that was the old bug that produced inflated totals).
- For amount drift on a tracked bill (a prediction with `is_tracked=true` and `amount_drift` > 5%),
  add an inline note: `- [Payee]: [amount] — due [date] (predicted ~[predicted_amount], up X%)`.
- Omit "Auto-settled" and "Confirm needed" sections if reconciliation returned nothing.
- **Confirm-needed guidance:** For each `candidates` entry, include the payee, bill amount, due
  date, and the best-matching transaction candidate's merchant/amount/date. The owner can confirm
  via the `bill-reminder` skill or by directly updating the bill status.

### Step 4: Predictions Are Read-Only

`predict_bills` is for surfacing patterns so the **owner** can decide. **Do not** call `track_bill`
to persist a prediction as a bill — that pollutes the obligations list and is what caused predicted
charges (e.g. variable, pay-on-completion services) to later appear as fixed overdue bills. Only
track a bill when there is a real, confirmed obligation (a statement, invoice, or the owner's
instruction). When you do track an auto-debited bill, set `autopay=true`.

### Step 5: Deliver Once

Call `notify` **exactly once**, at the very end, with the fully composed digest:

```python
notify(channel="telegram", intent="send", message=<formatted_digest>)
```

Use `intent="send"` (proactive scheduled delivery). **Never** send a draft and then a corrected
version — compose completely, then send a single message. Sending twice is a defect.

## Exit Criteria

- `reconcile_bills(lookback_days=90)` called first (settlement backstop)
- `upcoming_bills(days_ahead=14, include_overdue=True)` and `predict_bills(days_ahead=30)` called after
- Nothing to report across all sections → session exits without sending
- Otherwise: a single `notify(intent="send")` with reconciliation results first (if any), then
  an action-first digest where the header total is `needs_action_amount`, auto-pays/predictions
  are no-action context, and $0 placeholders are absent
- No `track_bill` call persisting a prediction
- Session exits after the single delivery
