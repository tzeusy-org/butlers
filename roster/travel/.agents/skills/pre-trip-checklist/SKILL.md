---
name: pre-trip-checklist
description: Pre-departure preparation workflow with actionable checklist, time-sensitive reminders, and completion verification
version: 1.0.0
tools_required:
  - upcoming_travel
  - trip_summary
  - add_document
  - calendar_create_event
  - memory_recall
  - memory_store_fact
---

# Pre-Trip Checklist Skill

## Purpose

This skill provides a comprehensive, actionable pre-departure preparation workflow triggered 5 days before travel. It systematically guides users through document verification, packing, confirmations, and time-sensitive tasks to ensure nothing is forgotten.

## When to Use

- 5 days before any scheduled departure
- User is preparing for an upcoming trip
- User needs a structured reminder of what to confirm before leaving
- User wants verification that everything is ready

## Prerequisite Context

Before starting the pre-trip checklist:
1. Retrieve trip details: `trip_summary(trip_id=<id>, include_documents=true, include_timeline=true)`
2. Identify all legs and accommodations
3. Check for any missing documents or late bookings
4. Verify passport and visa status from memory: `memory_recall(topic="passport")`

## Pre-Trip Checklist Workflow

Follow this structured flow in order. Use progressive disclosure — present one section at a time, confirm completion before moving to the next. Estimate total time: 15–30 minutes depending on trip complexity.

### Phase 1: Critical Documents (5 min)

Verify all essential travel documents are in hand and accessible.

**Step 1.1: Passport**
- Display: "Your passport [number, if available]: Expires [expiry_date]"
- Verify: "Is your passport still valid? Check expiry date."
- Warn if: Expiry is within 6 months of trip end date, or visa requirements are tight
- Ask: "Is your passport in hand and accessible?"
  - If no: "Get it now — you'll need it for check-in. If it's lost or damaged, contact passport services ASAP."
  - If yes: ✅ Mark complete

**Step 1.2: Visa (if required)**
- Check trip destination: Does it require a visa for your nationality?
- If not required: ✅ Skip and mark "no visa required"
- If required:
  - Ask: "Did you apply for and receive your visa?"
  - If no: 🚨 **Critical blocker** — "You cannot travel without a visa. Contact the embassy immediately. Processing times vary."
  - If yes: Ask: "Do you have the visa approval or stamp in your passport?"
    - If no (e-visa, border pass, etc.): "Make sure you have confirmation email or approval. Screenshot it if possible."
    - If yes: ✅ Mark complete

**Step 1.3: Boarding Passes**
- Retrieve: Check trip summary for all flight details
- For each flight:
  - Ask: "Have you downloaded or printed your boarding pass for [Flight details]?"
  - Note: "Typically available 24 hours before departure."
  - If not yet available: "Check-in opens tomorrow at 11:15 am [if 24h window is tomorrow]. Set a phone reminder if you want."
  - If available: Ask user to screenshot/download and attach: `add_document(trip_id=<id>, type="boarding_pass", blob_ref=<attachment>, metadata={"flight": "<flight_id>", "seat": "<seat>"})`
  - ✅ Mark complete once downloaded

**Step 1.4: Hotel Confirmations**
- Retrieve: All accommodations from trip summary
- For each accommodation:
  - Display: "[Hotel Name], [Check-in Date]–[Check-out Date], Confirmation: [Conf#]"
  - Ask: "Do you have your hotel confirmation? (Email or screenshot)"
  - If yes: Attach if available: `add_document(trip_id=<id>, type="accommodation_confirmation", blob_ref=<attachment>, metadata={"hotel": "<name>", "conf": "<conf_num>"})`
  - ✅ Mark complete

**Step 1.5: Travel Insurance**
- Ask: "Do you have travel insurance for this trip?"
- If no: "Travel insurance covers flight cancellations, lost luggage, medical emergencies. It's highly recommended, especially internationally. Cost: typically 3–8% of trip cost. Buy now?"
- If yes:
  - Ask: "Do you have the insurance confirmation?"
  - Attach: `add_document(trip_id=<id>, type="travel_insurance", blob_ref=<attachment>, metadata={"provider": "<provider>", "policy_number": "<policy>"})`
  - ✅ Mark complete
  - Remind: "Keep the policy number and emergency contact phone handy."

**Step 1.6: Other Travel Documents**
- Ask: "Are there any other critical documents? (e.g., vaccination records, prescriptions, address for hotel registration)"
- For each:
  - Attach: `add_document(trip_id=<id>, type="travel_document", blob_ref=<attachment>, metadata={"doc_type": "<type>"})`

**Section Summary:**
- Display: ✅ Completed documents; ⚠️ Pending items; 🚨 Critical blockers
- If critical blocker: "You cannot travel until this is resolved. Address it immediately."
- If pending: "Try to complete these by tomorrow."
- If all ✅: "Great! Documents are in order. Next section."

---

### Phase 2: Flight & Accommodation Confirmations (5 min)

Verify all bookings are confirmed and contact details are ready.

**Step 2.1: Flight Confirmations**
- For each flight, ask:
  - "Did you receive a booking confirmation email from [Airline]?"
  - "Do you know your PNR [if available] or confirmation number?"
  - "Have you checked that the flight is still scheduled?" (Confirm no unexpected cancellations/delays)
- If unsure: "Check your email (look for '[Airline] Booking Confirmation') or log into the airline's website."
- If no confirmation received: "Contact the airline immediately — your seat may not be reserved."
- Store seat assignments: "What seat were you assigned, or will you select at check-in?"
  - If assigned: `memory_store_fact(subject="user", predicate="flight_seat_assignment", content="<flight_id>: <seat>")`
- Remind: "Arrive 2–3 hours early for international flights, 1.5 hours for domestic."

**Step 2.2: Accommodation Confirmations**
- For each hotel, ask:
  - "Did you receive a booking confirmation from [Hotel]?"
  - "Do you know your confirmation number?"
  - "Have you verified the dates and room type?" (Match check-in/checkout to trip plan)
- If discrepancy: "Call the hotel directly to correct it before you arrive."
- Ask: "Did you arrange your preferred room type?" (E.g., king non-smoking, high floor, quiet area)
  - If not: "Call now or leave a note in your booking — most hotels will try to accommodate."

**Step 2.3: Cancellation Policies**
- Ask: "Do you know the cancellation deadlines for your flights and hotel?"
- Display in summary: "Cancellation deadlines: [Flight] until [date/time], [Hotel] until [date/time]"
- Reminder: "Mark these in your calendar so you don't accidentally forfeit the booking."

**Step 2.4: Contact Information**
- Ask: "Do you have the airline and hotel phone numbers saved?"
- Provide: "[Airline] 24/7 hotline: [number] | [Hotel] front desk: [number]"
- Suggest: "Save these to your phone in case you need to reschedule or have a question."

**Section Summary:**
- Display: All flight and accommodation confirmations with key dates, times, and reference numbers
- Checklist:
  - ✅ Boarding passes downloaded
  - ✅ PNRs/confirmation numbers verified
  - ✅ Seats assigned or noted
  - ✅ Check-in/check-out dates confirmed
  - ✅ Cancellation deadlines recorded

---

### Phase 3: Travel Logistics (5 min)

Confirm ground transport and logistics are arranged.

**Step 3.1: Airport Transport to Accommodation**
- Ask: "How will you get from the airport to your hotel?"
- If arranged: "Great! Do you have the driver's contact, booking confirmation, or directions saved?"
  - If using ride-share (Uber, taxi): "Do you have the app downloaded and payment method set up?"
  - If using public transport: "Do you know which train/bus line to take? Download a transport map app if helpful."
  - If car rental: "Do you have reservation confirmation, driver's license, and credit card ready?"
- If not yet arranged:
  - Suggest: "Options: Uber/Lyft (convenient, higher cost), taxi (plentiful at major airports), public transit (cheapest), hotel shuttle (sometimes free)"
  - Ask: "Want help deciding?"

**Step 3.2: Accommodation to Airport (Return)**
- Ask: "How will you get from your hotel to the airport for your return flight?"
- Important timing note: "Your return flight [time] requires departure time [departure_window]. Allow 2–3 hours for international."
- If late flight: "You might have time to check out and get a full breakfast before heading to the airport."
- If early flight: "Pre-arrange transport and confirm with hotel — they may offer early checkout."
- Verify arrangement: "Is transport booked or do you need to arrange?"

**Step 3.3: Luggage & Baggage Limits**
- Ask: "Do you know your baggage limits?"
- Retrieve airline policy: "[Airline] allows [carry-on limits] carry-on + [checked bag limits] checked bags. Overweight fees: $[amount] per bag."
- Advise: "Weigh your luggage before heading to the airport to avoid surprise fees."
- Packing tip: "Plan to pack light — carry-on only if possible to save time. If checking bags, allow extra time at check-in."

**Step 3.4: Ground Transport at Destination**
- Ask: "Do you have a way to get around at your destination?"
- Retrieve trip notes: [Planned activities and locations]
- Suggest based on destination:
  - **Tokyo**: "JR Pass covers trains. Download Google Maps and a transit app."
  - **Rural areas**: "Rental car recommended; public transit may be limited."
  - **Urban with metro**: "Metro pass/card often saves money and time vs. taxis."
- Ask: "Do you need to book or purchase transport now?"

**Step 3.5: Travel Insurance & Emergency Contacts**
- Display: "[Insurance provider] 24/7 hotline: [number] | Policy #: [policy]"
- Remind: "Keep your insurance confirmation and policy number accessible at all times."
- Ask: "Do you have emergency contact information for family/friends stored?"

**Section Summary:**
- Checklist:
  - ✅ Outbound transport (airport to hotel) arranged
  - ✅ Return transport (hotel to airport) arranged and timed correctly
  - ✅ Destination ground transport planned
  - ✅ Baggage limits verified
  - ✅ Insurance and emergency contacts accessible

---

### Phase 4: Packing & Personal Preparation (10 min)

Guide systematic packing and health/safety preparation.

**Step 4.1: Packing Strategy**
- Ask: "Are you a planner or a last-minute packer?"
- Offer: "I recommend laying out essentials today, then packing 2 days before departure."
- Ask: "How many days of clothes? (Remember: laundry is available most places if needed.)"

**Step 4.2: Essential Item Checklist**
- Display interactive checklist:

```
📋 Packing Checklist

DOCUMENTS & ESSENTIALS
☐ Passport / ID
☐ Boarding passes (printed or digital)
☐ Travel insurance confirmation
☐ Hotel confirmations
☐ Airline loyalty program info (for upgrades, etc.)
☐ Emergency contact list

MONEY & CARDS
☐ Credit/debit cards (notify bank you're traveling)
☐ Some local cash (especially if international)
☐ Emergency credit card (keep separate from main wallet)
☐ Receipts/expense tracker (if tracking for reimbursement)

TOILETRIES & MEDICATIONS
☐ Prescription medications (in original bottles, with label)
☐ Over-the-counter meds (pain reliever, cold medicine, etc.)
☐ Glasses / contacts + solution (if applicable)
☐ Chargers for phone, laptop, etc. + adapters (if international)
☐ Toothbrush, toothpaste, deodorant
☐ Shampoo, conditioner (or plan to buy on arrival)
☐ Sunscreen
☐ Any special health items (epi-pen, inhaler, etc.)

CLOTHING
☐ Comfortable walking shoes (already worn-in)
☐ Weather-appropriate outerwear
☐ 5–7 days of underwear + socks
☐ Comfortable travel outfit
☐ 1–2 casual outfits
☐ 1–2 nicer outfits (if needed for activities)
☐ Sleepwear
☐ Athletic/casual layer (hoodies, cardigans)

TECH & ACCESSORIES
☐ Phone + charger
☐ Laptop / tablet (if needed)
☐ Headphones
☐ Power bank (for phone charging on the go)
☐ Universal power adapter (if international)
☐ Travel-size hand sanitizer
☐ Earbuds / noise-canceling headphones (for flights)

OPTIONAL (DESTINATION-SPECIFIC)
☐ Swimsuit (if beach/pool activities)
☐ Gym clothes (if planning workouts)
☐ Hiking boots (if outdoor activities planned)
☐ Formal shoes (if evening activities)
☐ Day backpack / crossbody bag
☐ Compression bags (saves luggage space)
☐ Ziploc bags (for wet items, small valuables)
```

- For each category:
  - Ask: "Do you need items from this category?"
  - If yes: Help user identify specific items needed
  - Store: `memory_store_fact(subject="user", predicate="packing_reminders", content="<items to pack>", permanence="volatile")`

**Step 4.3: Special Items for Destination**
- Retrieve: Trip details and planned activities
- Suggest:
  - **Tokyo**: "Comfortable walking shoes (Shibuya and Shinjuku = lots of walking). Light layers."
  - **Mountain hiking**: "Proper hiking boots, moisture-wicking shirt, water bottle."
  - **Beach**: "Swimsuit, cover-up, water-resistant bag."
  - **Cold destination**: "Thermal layers, gloves, hat, scarf."
- Ask: "Are there destination-specific items you need?"

**Step 4.4: Health & Safety Preparation**
- Ask: "Do you have any health concerns for this trip?"
- If traveler's diarrhea risk (developing countries): "Consider over-the-counter anti-diarrhea medication."
- If altitude concern: "Check altitude of destination; consult doctor if you have cardiac concerns."
- If jet lag anticipated: "Melatonin or other sleep aids can help. Start adjusting sleep schedule 2–3 days before if possible."
- Ask: "Do you have adequate medications for the entire trip?"
  - If no: "Refill prescriptions before you leave. It's hard to get foreign prescriptions filled."

**Step 4.5: Money & Payment Preparation**
- Ask: "Have you notified your bank/credit card company about international travel?"
- Recommend: "Call 24 hours before departure. This prevents fraud alerts that can block your card."
- Ask: "Do you have local currency or a plan to get cash on arrival?"
- Remind: "ATMs are reliable in most major cities. Withdraw cash when you land."
- Ask: "Do you know exchange rates?" (Quick reference: "Current rate: 1 USD = [rate] [currency]")

**Step 4.6: Digital Preparation**
- Ask: "Do you need any apps for your destination?"
  - Suggest: Google Maps (offline maps available), local transit app, hotel booking/confirmation app, translation app
- Ask: "Have you set up international roaming or a local SIM plan?"
- Recommend: "Use airport WiFi or buy a local SIM for data. Ask hotel about WiFi on arrival."

**Section Summary:**
- Display: Completion % of packing checklist
- Deadline: "Pack 2 days before departure; do final packing the night before."
- Reminder: "Wear your heaviest shoes on travel day to save luggage space."

---

### Phase 5: Final 24-Hour Check (5 min)

Last-minute verification 24 hours before departure.

**Step 5.1: Flight Reconfirmation**
- Retrieve: Outbound flight details
- Display: "[Airline] [Flight] — [Departure date/time] from [Airport, Terminal]"
- Ask: "Do you have your boarding pass downloaded or printed?"
  - If yes: "✅ Great! Screenshot your phone or print it as backup."
  - If not: "Download now from airline website or app. 24-hour check-in may be just opening."

**Step 5.2: Departure Time Reminder**
- Calculate: Arrival time at airport = departure time − 2–3 hours
- Display: "Arrive at airport by [time]. Today is [date]."
- Ask: "Do you need transportation to the airport? Is it arranged?"

**Step 5.3: Document & Luggage Verification**
- Ask: "Final check — do you have your passport, boarding pass, and luggage?"
- Ask: "Is your luggage packed and within weight limits?"

**Step 5.4: Home & Utilities Prep**
- Ask: "Before you leave, do you need to:"
  - Adjust thermostat / lights?
  - Stop mail / packages?
  - Water plants or prepare for pet care?
  - Lock windows / secure valuables?
  - Set out-of-office auto-reply?
- Store any reminders: `memory_store_fact(subject="user", predicate="pre_departure_home_tasks", content="<tasks>", permanence="volatile")`

**Step 5.5: Notify Bank & Contacts**
- Reminder: "If you haven't already, call your bank to notify them of travel."
- Ask: "Have you told trusted friends/family when you're leaving and returning?"

**Step 5.6: Calendar Confirmation**
- Display: Full trip timeline from calendar
  - Departure: [Date/Time/Airport]
  - Arrival: [Date/Time/Airport]
  - Hotel check-in(s): [Dates/Times]
  - Hotel check-out(s): [Dates/Times]
  - Return flight: [Date/Time/Airport]
  - Expected home: [Date/Time]

**Section Summary:**
- Final checklist:
  - ✅ Boarding pass downloaded
  - ✅ Departure time confirmed + transportation arranged
  - ✅ Luggage packed and verified
  - ✅ Home secured
  - ✅ Bank notified
  - ✅ Emergency contacts aware

---

### Phase 6: Safe Travel & Send-Off (2 min)

Closing message and ongoing support offer.

**Step 6.1: Celebration & Encouragement**
- Message: "You're all set! You're leaving [departure_date] for [destination] — have an amazing trip!"

**Step 6.2: On-Trip Support**
- Explain: "While you're away, I'll monitor your flights for delays or changes. If anything updates, I'll notify you immediately."
- Offer: "Need help with activities, restaurant reservations, or changes while traveling? Just message me."

**Step 6.3: Emergency Contact**
- Display: "Emergency contacts for your trip:"
  - Airline support: [Phone/email]
  - Hotel support: [Phone]
  - Travel insurance: [Phone]
  - Your embassy (if traveling far): [Phone]

**Step 6.4: Post-Trip Checkout**
- Offer: "When you return, send me photos or feedback about your trip. I'd love to hear how it went!"
- Storage note: "I'll save your preferences from this trip for future planning."

**Step 6.5: Safe Travels**
- Message: "Safe travels! Enjoy every moment. 🌍✈️"

---

## Time-Sensitive Reminders

### Automated Reminder Schedule

Use calendar events to trigger reminders at these key times:

| Days Before | Reminder Type | Action |
| --- | --- | --- |
| **5 days** | Pre-Trip Checklist (Full) | Trigger this entire skill |
| **3 days** | Document Deadline | Verify all documents in place |
| **2 days** | Packing Deadline | Lay out essentials, start packing |
| **1 day** | Final Confirmation | Verify flights, hotel, ground transport |
| **24 hours** | Check-In Window | If online check-in available, download boarding pass |
| **12 hours** | Departure Prep | Confirm departure time, alert to any delays |
| **6 hours** | Final Departure | Reminder to leave for airport |
| **Morning of return** | Return Trip Checkout | Remind about hotel checkout time and return flight |

### Notification Strategy

- **Telegram/Email reminders**: Proactive (user doesn't need to ask)
- **Calendar events**: Persistent visible reminders
- **Delay alerts**: Immediate (if flight status changes)

---

## Example Pre-Trip Checklist Conversation

**Trigger**: 5 days before Tokyo trip (April 15 departure)

**Bot:** "Hey! Your Tokyo trip leaves in 5 days (April 15). Let's make sure everything is ready. We'll go through documents, confirmations, logistics, and packing. Should take about 30 minutes. Ready?"

**User:** "Yes, let's do it."

**Bot:** "Great! First, the essentials. Is your US passport in hand and still valid?"

**User:** "Yes, it expires 2027."

**Bot:** "Perfect — you're good. No visa needed for Japan. ✅ Next, have you downloaded your boarding pass for your United flight (UA 837)?"

**User:** "Not yet. Doesn't check-in open tomorrow?"

**Bot:** "Exactly! Online check-in opens in 24 hours. I'll remind you tomorrow morning. Once you have it, send me a screenshot and I'll attach it to your trip. Next — hotel confirmations. You booked 3 hotels in your itinerary. Do you have confirmations for all three?"

**User:** "Yeah, I got emails from all three. Let me check my email... I have Shinjuku Granbell, Kyoto Prince, and Osaka Marriott confirmations."

**Bot:** "Awesome. Send me screenshots of those confirmations if you have them handy. In the meantime, quick question about ground transport — how will you get from the airport to your first hotel?"

**User:** "I was thinking about taking the train. Someone told me about the JR Pass."

**Bot:** "Great call! JR Pass is perfect for your Tokyo-Kyoto-Osaka itinerary. Can save you $100+. Have you bought it yet?"

**User:** "No, but I was planning to."

**Bot:** "Do it today or tomorrow — availability is usually fine, but don't wait until the last minute. You can pick it up at the airport or order it in advance. Now, travel insurance — do you have that?"

**User:** "No, I haven't bought any."

**Bot:** "Definitely recommend it — covers medical emergencies, flight cancellations, lost luggage. Usually $50–150 for a 2-week international trip. Want to buy before you go?"

**User:** "Yeah, I'll grab some today."

**Bot:** "Perfect. Once you have it, send me the confirmation. Now let's talk packing. Do you know your baggage limits?"

**User:** "I think United allows 1 carry-on and 1 checked bag?"

**Bot:** "Correct — United includes 1 free checked bag for international. Carry-on must fit in the overhead bin. Anything over 50 lbs (23 kg) on your checked bag is an extra fee. Plan to weigh your bag before you head to the airport. Any special items you're bringing? Hiking boots, camera equipment?"

**User:** "Just a camera and normal clothes. I might buy stuff in Japan though."

**Bot:** "Smart packing mindset! Leave room for souvenirs. Here's a packing checklist for you — let me know if you need anything special for Tokyo/Kyoto/Osaka specifically:"

**Bot:** [Displays checklist]

**User:** "This is helpful. I think I have everything on here."

**Bot:** "Great! One more thing — have you notified your bank about traveling to Japan?"

**User:** "Oh no, I haven't!"

**Bot:** "Do that today — call their number on the back of your card and tell them you're traveling April 15–29. It prevents fraud alerts that can block your card when you try to use ATMs or pay in Japan."

**User:** "Will do!"

**Bot:** "Perfect! You're in really good shape. Here's your summary:

✅ **Critical items:** Passport valid, no visa needed, no travel insurance yet (buy today)
✅ **Confirmations:** Flight (boarding pass downloads tomorrow), hotels (send screenshots when you get a chance)
⏳ **To-do:** Buy JR Pass, buy travel insurance, notify bank, confirm ground transport from airport
✅ **Packing:** Ready to go, leave room for souvenirs

Come back to me tomorrow after online check-in opens and we'll finalize boarding passes. You're gonna have an amazing trip!"

**User:** "Thank you! This was really helpful."

---

## Accessibility & Adaptations

### For Anxious Travelers
- Break checklist into smaller sections across multiple days
- More frequent check-ins and reassurance
- Provide checklists in written form for reference

### For Tech-Averse Users
- Print checklists (offer to create PDF version)
- Walk through step-by-step verbally
- Simplify digital requirements (e.g., "just screenshot, don't worry about formatting")

### For Last-Minute Travelers
- Compress timeline: Focus on critical items (documents, confirmations)
- Skip non-essentials if pressed for time (can be done during trip)
- Flag blockers immediately (missing visas, passport expiry)

### For Frequent Travelers
- Skip unnecessary repetition
- Offer only new or changed items
- Store preferences for faster future checklists

---

## Version History

- v1.0.0 (2026-02-23): Initial skill creation with comprehensive pre-departure checklist, 24-hour final check, and adaptive support

