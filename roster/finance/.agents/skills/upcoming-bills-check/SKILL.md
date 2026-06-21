---
name: upcoming-bills-check
description: Scheduled task skill — weekly bills check with reconciliation sweep, then an action-first digest: needs-action items first, auto-pays/predictions as quiet context. Sent once via notify intent=send.
version: 2.1.0
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

# Pattern-based predictions from transaction history (read-only — see Step 3).
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

### Step 2: Compose the Digest (single tool call — do NOT hand-build it)

Pass the three results straight into `compose_bills_digest`. This tool is the
**single source of truth** for the digest format and the early-exit decision — it
wraps the tested `compose_upcoming_bills_digest()` function. Do **not** re-derive
the message inline in prose; that re-introduces the drift this wiring exists to
remove.

```python
result = compose_bills_digest(sweep=reconciliation, bills=bills, predictions=predictions)
message = result["message"]
```

- `message` is the fully composed, Telegram-ready digest string, **or** `null` when
  there is nothing worth sending (the tool applies the early-exit rule itself).
- If `message` is `null`, send nothing and exit immediately (see Step 4).

**What the tool produces** (for reference only — you do not assemble this yourself):

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

The tool encodes these guarantees: only "Needs action" uses urgency framing (sorted
overdue → due today → due soon, then amount descending); empty sections are omitted;
the header total is `totals.needs_action_amount` only (never autopay/predicted/
placeholder amounts); reconciliation sections are omitted when the sweep returned
nothing. The early-exit (returning `null`) fires only when `auto_settled`,
`candidates`, `needs_action`, `autopay`, and untracked `predictions` are all empty.

### Step 3: Predictions Are Read-Only

`predict_bills` is for surfacing patterns so the **owner** can decide. **Do not** call `track_bill`
to persist a prediction as a bill — that pollutes the obligations list and is what caused predicted
charges (e.g. variable, pay-on-completion services) to later appear as fixed overdue bills. Only
track a bill when there is a real, confirmed obligation (a statement, invoice, or the owner's
instruction). When you do track an auto-debited bill, set `autopay=true`.

### Step 4: Deliver Once

If `message` from Step 2 is `null`, send nothing and exit. Otherwise call `notify`
**exactly once**, at the very end, with the message the tool returned verbatim:

```python
if message is not None:
    notify(channel="telegram", intent="send", message=message)
```

Use `intent="send"` (proactive scheduled delivery). Send the tool's `message`
**unmodified** — do not rewrite, re-order, or re-format it. **Never** send a draft
and then a corrected version. Sending twice is a defect.

## Exit Criteria

- `reconcile_bills(lookback_days=90)` called first (settlement backstop)
- `upcoming_bills(days_ahead=14, include_overdue=True)` and `predict_bills(days_ahead=30)` called after
- `compose_bills_digest(sweep, bills, predictions)` called to build the message — the digest is
  NEVER hand-composed in prose
- `message is None` (tool early-exit) → session exits without sending
- Otherwise: a single `notify(intent="send", message=<tool message>)` with the message passed
  through unmodified
- No `track_bill` call persisting a prediction
- Session exits after the single delivery
