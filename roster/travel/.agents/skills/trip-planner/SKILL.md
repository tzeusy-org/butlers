---
name: trip-planner
description: Guided workflow for planning a new trip with destination, dates, bookings, gaps, and calendar blocking
version: 1.0.0
tools_required:
  - record_booking
  - list_trips
  - trip_summary
  - update_itinerary
  - calendar_create_event
  - memory_recall
  - memory_store_fact
---

# Trip Planner Skill

## Purpose

This skill provides a structured, interactive workflow for planning a new trip from scratch. Use this when starting a new travel project to capture destination, dates, preferred airlines/hotels, and guide users through booking and itinerary gaps until all legs, accommodations, and essential documents are identified.

## When to Use

- User wants to plan a trip to a new destination
- User has a rough idea (destination and dates) but no bookings yet
- User needs guidance on what bookings are required (flights, hotels, local transport)
- User wants to identify gaps in itinerary and fill them systematically

## Prerequisites

Before starting trip planning:
1. Clear recent trip context from memory: `memory_recall(topic="travel_plans")`
2. Determine if user has existing trips: `list_trips(status="planned")`
3. Check user travel preferences: `memory_recall(topic="travel_preferences")`

## Trip Planning Workflow

Follow this structured flow in order. Use progressive disclosure — ask one question at a time, confirm each major decision before moving forward.

### Phase 1: Trip Basics (5 min)

Establish destination, dates, and trip purpose.

**Step 1.1: Destination**
- Ask: "Where are you planning to go?"
- Accept: City, country, or airport name (e.g., "Tokyo", "Japan", "NRT")
- Store: `memory_store_fact(subject="trip", predicate="destination", content="<destination>")`
- If clarification needed (e.g., "Japan — which cities?"), use follow-up: "Any specific cities, or will you base yourself in Tokyo?"

**Step 1.2: Travel Dates**
- Ask: "What are your travel dates? (e.g., March 15–22)"
- Accept: Flexible formats — relative ("next month"), specific ("March 15"), or range ("March 15 to 22")
- Validate: Check for reasonable trip length (1–120 days), flag if suspiciously short/long
- If vague: Ask for departure and return separately
- Store: `memory_store_fact(subject="trip", predicate="departure_date", content="<ISO date>")`
- Store: `memory_store_fact(subject="trip", predicate="return_date", content="<ISO date>")`

**Step 1.3: Trip Purpose**
- Ask: "What's the trip for? (e.g., vacation, business, visiting family)"
- Accept: Any text, store for context
- Store: `memory_store_fact(subject="trip", predicate="purpose", content="<purpose>")`
- This informs booking preferences (e.g., business trips → frequent direct flights, longer layovers avoided)

**Step 1.4: Travelers**
- Ask: "How many travelers? Any children?"
- Accept: Number or description (e.g., "2 adults, 1 child age 8")
- Store in trip container for accurate pricing/seat needs

**Summary Step:**
- Confirm: "Got it — you're planning a trip to [destination] from [departure_date] to [return_date] ([duration] days) for [purpose] with [travelers]. Sound right?"
- If no: Loop back to step 1.1
- If yes: Move to Phase 2

### Phase 2: Flight Planning (10–15 min)

Guide through outbound and return flight selection.

**Step 2.1: Departure Airport**
- Ask: "Where will you depart from?"
- Retrieve hint: `memory_recall(topic="known_airport")` — if user has a preferred/home airport, suggest it
- Accept: City or IATA code (e.g., "San Francisco", "SFO")
- Store in trip container: `departure_airport`

**Step 2.2: Return Airport**
- Ask: "Where will you return to? (Same as departure, or different?)"
- Suggest: "Usually the same as where you left from, but some people do open-jaw itineraries."
- Store: `return_airport`

**Step 2.3: Outbound Flight Preferences**
- Ask: "Do you have preferences for the outbound flight?"
  - What time of day? (e.g., "morning", "evening", "flexible")
  - Airline preference? (e.g., "United", "ANA", "any")
  - Direct flight or okay with connections? (e.g., "direct only", "1 stop okay")
- Retrieve hint: `memory_recall(topic="preferred_airline")` — suggest if known
- Retrieve hint: `memory_recall(topic="preferred_seat")` — note for later

**Step 2.4: Return Flight Preferences**
- Ask: "Same preferences for return? Or different?"
- Example: "Some people prefer evening outbounds to maximize time before departure, but morning returns to get home early."

**Step 2.5: Booking Status**
- Ask: "Have you already booked any flights, or are you still deciding?"
- If already booked:
  - Ask for confirmation email or booking reference (PNR)
  - `record_booking(payload=<booking_details>)` — parse and store
  - Move to Step 2.6
- If not booked:
  - Offer guidance: "What airlines are you considering? I can help you think through options."
  - Suggest checking airline websites or aggregators (Kayak, Google Flights)
  - Note: "Once you've booked, send me the confirmation and I'll add it to your trip container."

**Step 2.6: Calendar Block**
- Create calendar block for each flight once booked:
  - Outbound: `calendar_create_event(title="✈ [Departure] → [Arrival]", start_at=<departure_time>, end_at=<arrival_time>, description="PNR: [PNR] | Terminal: [terminal] | Gate: [gate, if known]")`
  - Return: Similar format
- Create 24-hour online check-in reminder (if airline supports it)

### Phase 3: Accommodation Planning (5–10 min)

Guide through accommodation selection and booking.

**Step 3.1: Accommodation Type**
- Ask: "Where will you stay? (e.g., hotel, Airbnb, staying with friends, mix)"
- If multiple destinations, ask per city: "In [city], where are you thinking of staying?"

**Step 3.2: Hotel Preferences**
- Retrieve hint: `memory_recall(topic="hotel_preference")` — suggest known preferences
- Ask: "Do you have a preferred hotel chain or room type? (e.g., Marriott, king non-smoking)"
- Ask: "Any must-haves? (e.g., near metro, business center, gym, pool)"

**Step 3.3: Booking Status**
- Ask: "Have you booked accommodation yet?"
- If yes:
  - Request confirmation (email, screenshot, or booking ref)
  - `record_booking(payload=<accommodation_details>)` — parse and store
  - Move to Step 3.4
- If no:
  - Offer guidance: "Popular sites: Booking.com, Airbnb, hotels.com. I'd suggest filtering by location first (near attractions/transport), then price/reviews."
  - Note: "Once you book, send me the confirmation and I'll add it to your trip."

**Step 3.4: Accommodation Calendar Block**
- Create calendar block for each accommodation:
  - `calendar_create_event(title="🏨 [Hotel Name]", start_at=<check_in_date>, end_at=<check_out_date>, description="Confirmation: [conf_num] | Address: [address] | Check-in: [time if known]")`
- Note check-in time if provided (usually 15:00); set a reminder 2 hours before if specific time is known

**Step 3.5: Gap Detection**
- Check nights covered: Does accommodation match trip dates?
- Warn if gaps:
  - "You're arriving March 15 evening, but hotel check-in starts March 16. Are you taking a separate place for March 15, or arriving early?"
  - "You're checking out March 22 morning, but flight is 11:00 am. Will the hotel allow late checkout?"

### Phase 4: Local Transport & Activities (5–10 min)

Guide through ground transport and activity identification.

**Step 4.1: Airport to Accommodation**
- Ask: "How will you get from the airport to your hotel?"
  - Options: Taxi, Uber/Lyft, public transport, rental car, hotel shuttle, arrange pickup
- If relevant:
  - Ask about booking: "Already booked, or will you arrange on arrival?"
  - Suggest pre-booking if risky (e.g., arriving late at night)

**Step 4.2: Return to Airport**
- Ask: "How will you get back to the airport for your return flight?"
  - Similar options as 4.1
- Warn: "Your return flight is at 11:00 am. Allow 2–3 hours before departure. Plan transport accordingly."

**Step 4.3: Activities & Must-See Locations**
- Ask: "What are the must-see places or activities for [destination]?"
- Listen: Capture 3–5 key attractions or activities
- Store: `memory_store_fact(subject="trip", predicate="planned_activities", content="<activity list>")`
- Note: "You don't need to book everything now, but these help me suggest timing and transport."

**Step 4.4: Time Zone & Duration Reminder**
- Inform: "You're traveling to [timezone]. Time difference: [offset from user's timezone]."
- Suggest: "Jet lag tip: If crossing 5+ time zones, plan to rest the first day or split activities."

### Phase 5: Essential Documents & Insurance (5 min)

Identify document needs and insurance gaps.

**Step 5.1: Passport & Visa**
- Ask: "Does your passport need a visa for [destination]?"
- Retrieve hint: `memory_recall(topic="passport_expiry")` — check passport validity
- Warn if expiry is within 6 months: "Your passport expires [date]. Most countries require 6 months validity. Check before you go."
- If visa needed:
  - Ask: "Have you applied for a visa?"
  - If no: "Many countries have online visa systems. Check the embassy website or apply ASAP if it's a slow process."

**Step 5.2: Travel Insurance**
- Ask: "Do you have travel insurance? (Covers flight cancellation, medical emergencies, luggage)"
- If no: "Travel insurance is highly recommended, especially for international trips. Cost: typically 3–5% of trip cost, covers emergencies."
- If yes: "Where's your insurance confirmation? I'll attach it to your trip."

**Step 5.3: Required Vaccinations**
- Inform: "Check CDC or your country's health authority for vaccination requirements for [destination]."
- Ask: "Any vaccines needed or recommended?"

**Step 5.4: Travel Advisories**
- Inform: "Current travel advisory for [destination]: [summary]"
- Example: "Level 1 (exercise normal precautions)" or "Level 2 (exercise increased caution)"

**Step 5.5: Document Attachment**
- Ask: "Can you send or upload confirmation emails for your flights, hotel, and insurance?"
- `add_document(trip_id=<id>, type="booking", blob_ref=<attachment>)` — attach each
- This builds a searchable document library for pre-trip reference

### Phase 6: Trip Summary & Final Checks (5 min)

Generate a complete trip overview and identify remaining gaps.

**Step 6.1: Generate Trip Summary**
- Call: `trip_summary(trip_id=<id>, include_documents=true, include_timeline=true)`
- Display in this format:

```
📍 Trip: [Destination] — [Dates] ([Duration] days)
Purpose: [Purpose]

✈ Flights
- Outbound: [Departure Date & Time] [Airport] → [Arrival Date & Time] [Airport]
  Airline: [Airline] | Confirmation: [Conf#] | Status: [Booked/Pending]
- Return: [Similar]
  Estimated cost: $[if known]

🏨 Accommodation
- [Hotel/Place 1]: [Check-in] – [Check-out] | Confirmation: [Conf#]
  Address: [Address] | Cost: $[if known]
[Repeat for multiple locations]

🚕 Ground Transport
- Airport → Hotel: [Method] | Status: [Booked/TBD]
- Hotel → Airport: [Method] | Status: [Booked/TBD]

📋 Documents
- Passport: ✅ (expires [date])
- Visa: ⚠️ (if required, check status)
- Insurance: ✅ (attached)
- Boarding Pass: ⏳ (available 24h before flight)

🎯 Planned Activities
- [Activity 1]
- [Activity 2]
- [Activity 3]

⏰ Pre-Departure Checklist
- [ ] Book any outstanding flights/accommodation
- [ ] Arrange ground transport if needed
- [ ] Check passport validity (6+ months)
- [ ] Verify visa status (if required)
- [ ] Download travel insurance confirmation
- [ ] Notify bank/credit card company of travel
- [ ] Pack and prepare (use Pre-Trip Checklist skill 5 days before)
```

**Step 6.2: Identify Gaps**
- Flag incomplete items:
  - "You haven't booked return accommodation for March 20–21. Should I help you find options?"
  - "No ground transport booked for airport pickup. Want to arrange that now?"
  - "Visa required but status unknown. That's high priority."
- Assign severity:
  - **Critical** (missing flights, visa): Address immediately
  - **Important** (accommodation, insurance): Address within 2 weeks
  - **Nice-to-have** (activity bookings, restaurant reservations): Address as desired

**Step 6.3: Store Trip Memory**
- Save to butler memory for future context:
  - `memory_store_fact(subject="user", predicate="travel_trip_planned", content="[Destination] [Dates]")`
  - `memory_store_fact(subject="[destination]", predicate="recent_trip", content="[Trip summary]")`

**Step 6.4: Set Reminders**
- Create upcoming reminders:
  - 3 weeks before departure: "Final booking deadline for accommodation/activities"
  - 2 weeks before: "Check visa status if required"
  - 1 week before: "Confirm flights and get booking references ready"
  - 5 days before: "Trigger Pre-Trip Checklist"
  - 24 hours before: "Online check-in opens; confirm flight details"

**Step 6.5: Final Confirmation**
- Ask: "Is this trip plan complete, or are there other bookings/details to add?"
- If yes: Loop back to relevant phase
- If no: Move to next section

### Phase 7: Closing & Support Offer (2 min)

Wrap up and offer ongoing support.

**Step 7.1: Celebration**
- Affirm: "Awesome! Your trip to [destination] is all set! You're leaving [departure_date]."

**Step 7.2: Ongoing Support**
- Explain: "I'll monitor your trip for any changes from airlines/hotels via email. I'll also send you pre-trip reminders and the pre-trip checklist 5 days before you leave."
- Offer: "Need help with anything else? Restaurant reservations, activity bookings, itinerary adjustments?"

**Step 7.3: Store Contact**
- "If plans change, just send me an update and I'll adjust everything."

---

## Gap Detection Heuristics

### Critical Gaps (Address Immediately)

- **Missing outbound flight** for planned departure date
- **Missing accommodation** for any night of the trip
- **Visa required but not obtained** (and time to process is tight)
- **Passport expiring within 6 months** of trip end date

### Important Gaps (Address Within 1–2 Weeks)

- **No return flight booked** yet
- **Airport ground transport not arranged** (especially late-night/early-morning arrivals)
- **Travel insurance not purchased**
- **Return accommodation not booked** (if multi-leg trip)

### Nice-to-Have Gaps (Address as Desired)

- **Specific activities not yet booked** (can often be done on-site)
- **Restaurant reservations not confirmed** (popular places, special occasion)
- **Travel guide or packing list not prepared**

---

## Adaptive Tips

### For Solo Travelers
- Ask about safety preferences: "Any interests in group activities or staying in social accommodations?"
- Suggest: "Solo travelers often book activities with group tours to meet other travelers."
- Storage: `memory_store_fact(subject="user", predicate="travel_style", content="solo")`

### For Group Trips (2+ travelers)
- Ask: "Who else is traveling? How many people to coordinate?"
- Clarify ownership: "Who's making bookings? I'll reference their confirmation emails."
- Warn: "Group bookings sometimes need all travelers' passport info upfront. Have everyone ready."

### For Business Trips
- Ask: "Do you need specific accommodations for meetings? (e.g., internet, business center, quiet area)"
- Suggest: "Business travelers often use loyalty programs for status/upgrades. Connected to your frequent flyer account?"
- Storage: `memory_store_fact(subject="trip", predicate="trip_type", content="business")`

### For Budget-Conscious Travelers
- Suggest: "Booking flights on Tuesday/Wednesday is often cheaper. Do you have flexibility on dates?"
- Offer: "I can watch for price drops on your flights and alert you to savings."
- Storage: `memory_store_fact(subject="user", predicate="travel_style", content="budget")`

### For Last-Minute Trips
- Ask: "When do you leave? (Less than 1 week is tight for visa/insurance.)"
- Prioritize: Focus on flights and accommodation first; skip non-essential nice-to-haves
- Warn: "Fast-track visa processing (if available) costs extra."

---

## Example Conversation Flow

**User:** "I want to plan a trip to Japan in April."

**Bot:** "Exciting! Let's plan your Japan trip. First, roughly how long do you want to go for? (e.g., 1 week, 2 weeks, 3 weeks?)"

**User:** "About 2 weeks. Mid-April to end of April."

**Bot:** "Perfect — that's April 15–29 (14 days). Great time to visit! Is this just you, or will others be traveling?"

**User:** "Just me — it's a solo trip."

**Bot:** "Nice! And what's the main purpose? Vacation, visiting friends, work?"

**User:** "Vacation — I want to see Tokyo, Kyoto, and maybe Osaka."

**Bot:** [Stores facts] "Got it — solo vacation to Japan, April 15–29, visiting Tokyo, Kyoto, and Osaka. Sound right?"

**User:** "Yes!"

**Bot:** "Great! Now let's plan your flights. Where will you depart from?"

**User:** "San Francisco (SFO)."

**Bot:** "And you'll return to SFO too?"

**User:** "Yes."

**Bot:** "Do you have airline preferences? I see you usually fly United."

**User:** "Yeah, if possible. I'd prefer a direct flight if they have one."

**Bot:** "United has a direct SFO–Tokyo (NRT or HND) flight. Let me check availability... Actually, I can't book, but I can help you think through it. For a solo trip, when do you prefer to depart? Morning, afternoon, or evening?"

**User:** "Afternoon/evening — I like to use the morning to pack and get to the airport relaxed."

**Bot:** "Smart! So you're thinking mid-afternoon to evening departure on April 15. You'll arrive in Tokyo around April 17 morning Japan time (international date line). That work?"

**User:** "Yes, that makes sense."

**Bot:** "Once you book the flight, send me the confirmation email and I'll add it to your trip. Now, where will you stay? Hotels, Airbnb, mix?"

**User:** "Probably hotels. I like mid-range chains, maybe Marriott or Hilton."

**Bot:** [Stores fact] "Got it — Marriott/Hilton mid-range preference noted. Have you booked any hotels yet?"

**User:** "Not yet. I'll do that after we plan."

**Bot:** "Sure! Quick overview: Tokyo (probably 5 nights), Kyoto (4–5 nights), Osaka (2–3 nights)? Or different split?"

**User:** "Maybe Tokyo 5, Kyoto 4, Osaka 2?"

**Bot:** "That's 11 nights of accommodation. You arrive April 17 morning, so check-in that day. Let's assume checkout April 28, flight April 29 late evening. That give you time to explore?"

**User:** "Yes, that's perfect."

**Bot:** "Once you book your hotels, send confirmations and I'll create calendar blocks. Now, ground transport — will you use taxis, Uber, public transport, or a Japan Rail pass?"

**User:** "I've heard the JR pass is good value for multiple cities."

**Bot:** "Totally! JR Pass is ideal for your itinerary. It covers Tokyo–Kyoto–Osaka trains. Have you thought about buying it before you leave or on arrival?"

**User:** "I'm not sure. Which is better?"

**Bot:** "Good Q: Buying in advance is often slightly cheaper and you're guaranteed availability. Buying on arrival is flexible but riskier if sold out. I'd suggest buying before, but either works."

**User:** "Okay, I'll look into it."

**Bot:** "Great. Now, activities — any must-see places?"

**User:** "Mount Fuji, Fushimi Inari, temples in Kyoto, Osaka Castle, teamLab, food experiences."

**Bot:** [Stores] "Love it! Those are all blockbuster experiences. Some quick notes: Mount Fuji is a day trip from Tokyo (often cloudy in April, but possible). Teamlab is super popular—book ahead. Temple tickets are cheap and walkable. You'll have plenty of time."

**User:** "Thanks! What about documents?"

**Bot:** "Good thinking! A few things: US passport good for 6+ months?"

**User:** "Yeah, it expires 2027."

**Bot:** "Perfect, no visa needed. Travel insurance? Have you got any?"

**User:** "No, I haven't."

**Bot:** "Definitely get some—covers flight cancellations, medical, luggage. Cost is usually $50–150 for a 2-week trip."

**User:** "Okay, I'll buy it."

**Bot:** "Once you have confirmations for flights, hotels, and insurance, send them over and I'll attach them all to your trip container. Then I'll create calendar blocks, set reminders, and you'll be golden."

**User:** "Perfect! Thanks for walking me through this."

**Bot:** "You got it! Safe travels—Japan in April is magical. Talk soon!" 🚀

---

## Version History

- v1.0.0 (2026-02-23): Initial skill creation with structured trip planning workflow, gap detection, and adaptive tips

