---
name: message-triage
description: Classify and route incoming messages to specialist butlers — full domain classifiers, routing safety rules, and worked examples
trigger_patterns:
  - "classify this message"
  - "which butler should handle"
  - "route this to"
  - "triage incoming"
---

# Message Triage Skill

## Purpose

This skill provides the complete classification and routing reference for the Switchboard. Use it to determine which specialist butler should receive an incoming message, how to decompose multi-domain messages, and how to handle edge cases and domain boundary conflicts.

## Available Butlers

- **finance**: Receipts, invoices, bills, subscriptions, transaction alerts, spending queries
- **relationship**: Contacts, interactions, reminders, gifts, social events
- **health**: Medications, measurements, conditions, symptoms, exercise, diet, nutrition
- **travel**: Flight bookings, hotel reservations, car rentals, trip itineraries, travel documents
- **education**: Personalized tutoring, quizzes, spaced repetition, learning progress
- **general**: Catch-all for anything that does not fit a specialist

---

## Classification Rules

### Finance Classification

Route to finance when the message involves:

- **Transaction/payment signals**: mentions of charged, paid, payment confirmed, invoice, receipt, total amount
- **Billing language**: due date, payment due, minimum payment, late fee, overdue
- **Subscription lifecycle**: renewal, auto-renew, subscription cancelled, price change, subscription paused
- **Financial alerts**: transaction alert, payment alert, statement ready, balance notification
- **Sender domain signals**: `@chase.com`, `@paypal.com`, `@amazon.com`, `@venmo.com`, `@wise.com`, `@stripe.com`, `@amex.com`, `@bill.com`
- **Subject line patterns**: "Your receipt", "Payment confirmed", "Statement ready", "Your invoice", "Payment due", "Subscription renewed", "Price change notice", "Auto-renewal reminder", "Transaction alert"
- **Spending queries**: "What did I spend?", "How much did I spend?", "Show my expenses", "What bills are due?"

### Travel Classification

Route to travel when the message involves:

- **Booking confirmations**: flight itinerary, hotel booking, car rental confirmation, boarding pass
- **Itinerary changes**: flight delay, gate change, rebooking, cancellation, schedule change
- **Travel logistics**: check-in reminder, departure time, layover, terminal, seat assignment
- **Travel documents**: visa, passport, travel insurance, boarding pass upload
- **Sender domain signals**: `@united.com`, `@delta.com`, `@aa.com`, `@southwest.com`, `@jetblue.com`, `@booking.com`, `@airbnb.com`, `@hotels.com`, `@expedia.com`, `@kayak.com`, `@tripadvisor.com`, `@hertz.com`, `@enterprise.com`, `@marriott.com`, `@hilton.com`
- **Subject line patterns**: "Your booking is confirmed", "Itinerary update", "Flight delay", "Check-in now", "Gate change", "Boarding pass", "Trip confirmation", "Reservation confirmed"
- **Trip queries**: "When does my flight leave?", "What's my hotel address?", "Show my trip", "What's my confirmation number?"

### Education Classification

Route to education when the message involves:

- **Explicit learning intent**: "teach me", "explain [topic] to me", "I want to learn", "help me understand", "I want to study"
- **Quiz or testing requests**: "quiz me", "test me on", "ask me questions about", "practice questions for", "can you quiz me"
- **Knowledge self-assessment**: "what do I know about", "how well do I know", "test my knowledge of", "do I know [topic] well"
- **Spaced repetition context**: "review session", "review [topic]" (when clearly educational, not calendar-review), "my learning reviews", "due for review"
- **Learning progress queries**: "how am I doing on [topic]", "what have I mastered", "my learning progress", "show me my mastery"
- **Curriculum or syllabus requests**: "create a curriculum for", "learning path for", "study plan for", "help me plan to learn"
- **Active learning phrases**: "I'm trying to learn", "I want to get better at", "I need to understand [topic]", "walk me through [topic]"

**Disambiguation rules for education routing:**

- "review my calendar" — NOT education — route to general or health/finance depending on context
- "explain this document" with no topic context — ambiguous; ask for clarification before routing
- "study [topic]" — education; "study break" or "study hall" — general
- "learn about" with a health topic (e.g., "learn about my medications") — health unless explicit tutoring intent is present
- "quiz me" — education, regardless of topic domain
- Finance, health, or travel questions that are informational requests (not tracking/logging) with explicit "teach me" framing — education

### Relationship Classification

Route to relationship when the message involves:

- A person, contact, relationship, gift, or social interaction
- Contact information, social reminders, or loan tracking between people

**Domain indicators:** person names, relationships (friend/family/colleague), social verbs (call/meet/visit), gifts, birthdays, contact details

**Example messages:**
- "Add John's birthday on March 15th"
- "Remind me to call Mom next Tuesday"
- "Log that I met Sarah for coffee today"
- "Gift idea for Alice: new headphones"

### Health Classification

Route to health when the message involves:

- Health, medication, symptoms, exercise, diet, food, meals, nutrition, or cooking

**Domain indicators:** body measurements, medical terms, symptoms, medications, food/meals, exercise, doctor/medical

**Example messages:**
- "Log my weight as 165 lbs"
- "I took my morning medication"
- "Log breakfast: oatmeal with berries"
- "Track blood pressure 120/80"

### General Classification

Route to general when:

- The message is unsure or does not fit a specialist domain
- The message is a list, task, note, or reminder without clear specialist context

---

## Routing Safety Rules

- **Finance vs general**: Finance wins tie-breaks when explicit payment, billing, or subscription semantics are present
- **Finance vs travel**: Finance should not capture travel itineraries unless the primary intent is billing/refund/payment resolution
- **Travel vs general**: Travel wins tie-breaks when explicit booking, itinerary, or flight semantics are present
- **Travel vs finance**: Travel should not capture financial transactions for travel services — those go to finance unless the primary intent is itinerary/booking, not expense tracking
- **Education vs general**: Education wins tie-breaks when explicit learning, teaching, or quizzing intent is present ("teach me", "quiz me", "what do I know about")
- **Education vs health**: Education should NOT capture health questions that are factual lookups without tutoring intent (e.g., "what does metformin do?" — health, not education; "teach me about diabetes" — education)
- **Education vs calendar**: "review" without educational context (e.g., "review my calendar") MUST NOT route to education
- **Ambiguous commerce/relationship**: Defer to Switchboard confidence policy and fallback routing contract

---

## Confidence Scoring

### HIGH confidence (>80%)

- Clear single-domain match
- Multiple strong domain signals
- No conflicting indicators
- **Action:** Route immediately to specialist butler

### MEDIUM confidence (40–80%)

- Weak domain signals or ambiguous phrasing
- Could fit multiple domains
- Context suggests specialist but not certain
- **Action:** Route to best-match butler with acknowledgment of uncertainty

### LOW confidence (<40%)

- No clear domain signals
- Highly ambiguous or multi-domain
- Insufficient context to classify
- **Action:** Default to general butler (catch-all)

---

## Decision Matrix

| Message Type | Signals | Confidence | Route To |
|---|---|---|---|
| Finance receipt | amount + "receipt"/"charged" | HIGH | finance |
| Subscription alert | renewal + brand | HIGH | finance |
| Flight booking | airline + confirmation code | HIGH | travel |
| Hotel reservation | hotel + dates + reservation ID | HIGH | travel |
| Teaching request | "teach me" + topic | HIGH | education |
| Quiz request | "quiz me" + topic | HIGH | education |
| Knowledge self-check | "what do I know about" + topic | HIGH | education |
| Medication question (factual) | drug name, no tutoring intent | HIGH | health |
| Calendar review | "review my schedule" | HIGH | general |
| Person's birthday | name + date | HIGH | relationship |
| Log medication | drug name + "take"/"log" | HIGH | health |
| Shopping list item | food/item + "list" | HIGH | general |
| "Call Mom" | name + social verb | HIGH | relationship |
| "Weight: 150" | number + health measurement | HIGH | health |
| "Remind me to..." (no context) | task verb only | LOW | general |
| "Had lunch with Sarah" | name + meal | MEDIUM | relationship |
| "Ate salad" | meal only | MEDIUM | health |

---

## Routing via `route_to_butler` Tool

For each target butler, call the `route_to_butler` tool with:

- `butler`: the target butler name (e.g. "finance", "health", "relationship", "travel", "education", "general")
- `prompt`: a self-contained sub-prompt for that butler
- `context` (optional): additional context

After routing, respond with a brief text summary of what you did.

### When to Decompose

- **Single-domain message**: Call `route_to_butler` once for the target butler
- **Multi-domain message**: Call `route_to_butler` once per domain, each with a focused sub-prompt
- **Ambiguous message**: Call `route_to_butler` for `general`

### Self-Contained Sub-Prompts

Each sub-prompt must be independently understandable. Include:

- Relevant entities (people, merchants, amounts, dates)
- Necessary context from the original message
- The specific action or information for that domain

---

## Worked Examples

### Example 1: Single-domain (no decomposition)

**Input:** "Remind me to call Mom next week"

**Action:** Call `route_to_butler(butler="relationship", prompt="Remind me to call Mom next week")`

**Response:** "Routed to relationship butler for social reminder."

---

### Example 2: Multi-domain (decomposition needed)

**Input:** "I saw Dr. Smith today and got prescribed metformin 500mg twice daily. Also, remind me to send her a thank-you card next week."

**Action:**
1. Call `route_to_butler(butler="health", prompt="I saw Dr. Smith today and got prescribed metformin 500mg twice daily. Please track this medication.")`
2. Call `route_to_butler(butler="relationship", prompt="I saw Dr. Smith today. Remind me to send her a thank-you card next week.")`

**Response:** "Routed medication tracking to health butler and thank-you card reminder to relationship butler."

---

### Example 3: Food preference (routes to health)

**Input:** "I like chicken rice"

**Action:** Call `route_to_butler(butler="health", prompt="I like chicken rice")`

**Response:** "Routed food preference to health butler for nutrition tracking."

---

### Example 4: Finance receipt (routes to finance)

**Input:** "I got a receipt from Amazon for $45.99 for a new keyboard"

**Action:** Call `route_to_butler(butler="finance", prompt="I got a receipt from Amazon for $45.99 for a new keyboard. Please track this transaction.")`

**Response:** "Routed transaction to finance butler for expense tracking."

---

### Example 5: Subscription notification (routes to finance)

**Input:** "Netflix charged me $15.99 — my subscription renewed"

**Action:** Call `route_to_butler(butler="finance", prompt="Netflix charged me $15.99 for subscription renewal. Please track this subscription renewal.")`

**Response:** "Routed subscription renewal to finance butler."

---

### Example 6: Ambiguous (default to general)

**Input:** "What's the weather today?"

**Action:** Call `route_to_butler(butler="general", prompt="What's the weather today?")`

**Response:** "Routed general query to general butler."

---

### Example 7: Flight booking confirmation (routes to travel)

**Input:** "Your booking is confirmed — Delta flight DL204, New York JFK to London LHR, departing March 5 at 10:30pm. Confirmation code: XKQP72."

**Action:** Call `route_to_butler(butler="travel", prompt="Delta flight DL204 from New York JFK to London LHR on March 5 at 10:30pm has been confirmed. Confirmation code: XKQP72. Please track this flight itinerary.")`

**Response:** "Routed flight booking confirmation to travel butler for itinerary tracking."

---

### Example 8: Travel vs finance boundary (routes to travel, not finance)

**Input:** "Marriott Bonvoy: Your reservation at Marriott Downtown Chicago is confirmed for April 12–14. Reservation ID: 98273650."

**Action:** Call `route_to_butler(butler="travel", prompt="Hotel reservation confirmed at Marriott Downtown Chicago for April 12–14. Reservation ID: 98273650. Please track this hotel booking.")`

**Response:** "Routed hotel booking confirmation to travel butler — primary intent is itinerary tracking, not expense tracking."

---

### Example 9: Learning request (routes to education)

**Input:** "Teach me how TCP handshakes work"

**Action:** Call `route_to_butler(butler="education", prompt="Teach me how TCP handshakes work. Start with a calibration to see what I already know about networking.")`

**Response:** "Routed to education butler for a personalized TCP/networking tutorial."

---

### Example 10: Quiz request (routes to education)

**Input:** "Quiz me on calculus"

**Action:** Call `route_to_butler(butler="education", prompt="Quiz me on calculus. Check what mind maps I have active for calculus and run a review or teaching session as appropriate.")`

**Response:** "Routed quiz request to education butler."

---

### Example 11: Knowledge self-assessment (routes to education)

**Input:** "What do I know about machine learning?"

**Action:** Call `route_to_butler(butler="education", prompt="The user wants to know what they currently know about machine learning. Check mastery state from mind maps and memory, and summarize their knowledge level.")`

**Response:** "Routed to education butler for learning progress query on machine learning."

---

### Example 12: Education vs health boundary (routes to health, not education)

**Input:** "What does metformin do?"

**Action:** Call `route_to_butler(butler="health", prompt="What does metformin do? (Context: user is asking for information about their medication, not requesting a tutoring session)")`

**Response:** "Routed medication question to health butler — factual lookup without explicit tutoring intent."

---

### Example 13: Calendar-review (does NOT route to education)

**Input:** "Review my schedule for tomorrow"

**Action:** Call `route_to_butler(butler="general", prompt="Review my schedule for tomorrow and summarize what's on the calendar.")`

**Response:** "Routed calendar review to general butler — 'review' here means calendar preview, not educational review."

---

## Multi-Domain Decomposition

### Decomposition Decision Tree

```
1. Does the message have ONE clear domain?
   YES → Route entire message to that butler
   NO → Continue to step 2

2. Does the message span MULTIPLE domains with clear boundaries?
   YES → Decompose into sub-prompts (one per butler)
   NO → Continue to step 3

3. Is the intent ambiguous or unclear?
   YES → Route to general butler (default)
   NO → Re-evaluate from step 1
```

### When NOT to Decompose

Do NOT decompose when:

1. **Single domain**: The message clearly belongs to one specialist butler
2. **Unclear boundaries**: You cannot clearly separate concerns between domains
3. **Ambiguous intent**: The user's intent is unclear or requires clarification
4. **Interdependent actions**: The actions require cross-butler coordination that decomposition would break

### Context Preservation in Sub-Prompts

When splitting a message, ensure critical context travels with each sub-prompt:

- **Named entities**: "Mom", "Dr. Smith", "Lisa" — include in relevant sub-prompt
- **Relationships**: "my brother", "my doctor" — preserve the relationship context
- **Temporal info**: "Tuesday", "next week", "at 3pm" — include in time-sensitive sub-prompts
- **Quantities**: "150mg", "3 miles", "twice daily" — keep units with the action

### Decomposition Examples

**Example A: Health + Relationship**

Input: "Remind me to take my blood pressure meds at 8am daily, and text my sister to check on her recovery"

```json
[
  {"butler": "health", "prompt": "Set up a daily reminder at 8am to take blood pressure medication"},
  {"butler": "relationship", "prompt": "Send a text message to my sister to check on her recovery"}
]
```

**Example B: Relationship + Health + General**

Input: "Schedule a dinner with Mom next Friday, log my weight as 185 lbs, and remind me to backup my computer this weekend"

```json
[
  {"butler": "relationship", "prompt": "Schedule a dinner with Mom next Friday"},
  {"butler": "health", "prompt": "Log weight measurement: 185 lbs"},
  {"butler": "general", "prompt": "Set a reminder to backup computer this weekend"}
]
```

**Example C: Complex multi-domain with context**

Input: "My doctor recommended I start taking Metformin 500mg twice daily, call Mom on Tuesday to tell her about the new prescription, and set up a monthly check-in with Dr. Chen starting next month"

```json
[
  {"butler": "health", "prompt": "Add new medication: Metformin 500mg, to be taken twice daily, as recommended by doctor"},
  {"butler": "relationship", "prompt": "Call Mom on Tuesday to tell her about the new Metformin prescription"},
  {"butler": "relationship", "prompt": "Set up a monthly recurring check-in with Dr. Chen, starting next month"}
]
```

---

## Implementation Notes

- Use the Switchboard's MCP tools to route messages to target butlers
- Log all decomposition decisions for audit trail
- If a butler returns an error, log it but continue processing other sub-prompts
- Aggregate responses from multiple butlers before returning to the user
