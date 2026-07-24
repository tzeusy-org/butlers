---
name: interactive-response
description: Finance-butler response-mode selection and worked examples for transactions, bills, subscriptions, and finance questions.
version: 1.0.0
tools_required:
  - record_transaction
  - track_bill
  - track_subscription
  - spending_summary
  - calendar_create_event
  - memory_recall
  - memory_search
  - memory_store_fact
  - notify
---

# Finance Interactive Response Skill

## Purpose

Load this skill when an interactive finance message needs a deliberate response
mode or a worked pattern for transactions, bills, subscriptions, and finance
questions.

### Response Mode Selection

Choose the appropriate response mode based on the message type and action taken:

1. **React**: Quick acknowledgment without text (emoji only)
   - Use when: The action is simple and self-explanatory
   - Example: User says "Paid the water bill" → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: The action needs a short confirmation with the key fact
   - Example: "Transaction logged: $45.00 at Trader Joe's (groceries)"

3. **Follow-up**: Proactive question or suggestion
   - Use when: You notice a pattern, can add context, or have a useful observation
   - Example: "This is the 3rd Netflix charge this month. Want me to create a subscription record?"

4. **Answer**: Substantive information in response to a question
   - Use when: The user asked a direct question about their finances
   - Example: User asks "What did I spend on dining this month?" → Answer with the aggregated total

5. **React + Reply**: Combined emoji acknowledgment with message
   - Use when: You want immediate visual feedback plus substantive context
   - Example: React with ✅ then "Subscription renewed: Spotify $9.99/month. Next renewal: March 15."

### Complete Examples

#### Example 1: Simple Transaction from Receipt (Affirm)

**User message**: "Coffee and lunch at Blue Bottle, $23.50"

**Actions**:
1. `record_transaction(posted_at=now, merchant="Blue Bottle Coffee", amount=-23.50, currency="USD", category="dining")`
2. `notify(channel="telegram", message="Logged: $23.50 at Blue Bottle Coffee (dining).", intent="reply", request_context=...)`

---

#### Example 2: Bill Payment Confirmation (React)

**User message**: "Just paid the electric bill"

**Actions**:
1. `memory_recall(topic="electric bill")`: retrieve known payee and amount
2. `track_bill(payee="PG&E", amount=84.00, currency="USD", due_date=<recalled>, status="paid", paid_at=now)`
3. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`

---

#### Example 3: Subscription Renewal Email (Affirm + Calendar)

**Trigger**: Email saying "Your Netflix subscription has renewed for $15.49"

**Actions**:
1. `track_subscription(service="Netflix", amount=15.49, currency="USD", frequency="monthly", next_renewal=<+1 month>, status="active", auto_renew=true, source_message_id=<email_id>)`
2. `record_transaction(posted_at=now, merchant="Netflix", amount=-15.49, currency="USD", category="subscriptions", source_message_id=<email_id>)`
3. `calendar_create_event(title="Netflix renewal", start_time=<next_renewal - 7 days>, notes="Auto-renews $15.49/month")`
4. `notify(channel="telegram", message="Netflix renewed: $15.49/month. Next renewal in 30 days. Reminder set.", intent="reply", request_context=...)`

---

#### Example 4: Spending Question (Answer)

**User message**: "How much did I spend last month?"

**Actions**:
1. `spending_summary(start_date=<last month start>, end_date=<last month end>, group_by="category")`
2. `memory_recall(topic="spending patterns")`
3. Synthesize top categories and total
4. `notify(channel="telegram", message="Last month you spent $1,243.60 total. Top categories:\n- Groceries: $380\n- Dining: $210\n- Subscriptions: $87\n- Transport: $74", intent="reply", request_context=...)`

---

#### Example 5: Subscription Cancellation (React + Reply)

**User message**: "I cancelled my Adobe subscription"

**Actions**:
1. `memory_search(query="Adobe subscription")`: find existing record
2. `track_subscription(service="Adobe Creative Cloud", status="cancelled", ...)`
3. `memory_store_fact(subject="Adobe Creative Cloud", predicate="subscription_status", content="cancelled by user", permanence="standard", importance=7.0, tags=["subscription", "cancelled"])`
4. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
5. `notify(channel="telegram", message="Adobe Creative Cloud marked as cancelled. That saves ~$54.99/month.", intent="reply", request_context=...)`

---

#### Example 6: Ambiguous Financial Email, Placeholder Bill (Follow-up)

**Trigger**: Email saying "Your statement is ready" from Chase

**Actions**:
1. Extract available data: institution=Chase, statement available, no amount
2. `memory_recall(topic="Chase account")`: retrieve known account details
3. `track_bill(payee="Chase Credit Card", amount=0.00, currency="USD", due_date=<extracted if present>, status="pending", source_message_id=<email_id>)`
4. `notify(channel="telegram", message="Chase statement ready. I've logged a placeholder bill (amount TBD). When you pay it, recording the debit will auto-settle the bill. Want to tell me the minimum payment due now so I can track the amount?", intent="reply", request_context=...)`

> **Placeholder bill semantics**: A `$0.00 pending` bill is a **placeholder awaiting
> reconciliation**, NOT a terminal unpaid obligation. Do NOT surface it as overdue or nag the
> user to act on it immediately. When the matching payment debit is recorded via
> `record_transaction`, the system backfills the amount and settles the bill automatically
> (deterministic `reconcile_bills` flow). Present it as "placeholder, will auto-settle on
> payment" rather than an unresolved debt requiring urgent attention.
