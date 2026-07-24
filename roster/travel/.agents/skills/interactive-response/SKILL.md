---
name: interactive-response
description: Travel-butler response-mode selection and worked examples for bookings, itinerary updates, documents, and trip questions.
version: 1.0.0
tools_required:
  - record_booking
  - update_itinerary
  - list_trips
  - trip_summary
  - upcoming_travel
  - add_document
  - calendar_create_event
  - calendar_update_event
  - memory_recall
  - notify
---

# Travel Interactive Response Skill

## Purpose

Load this skill when an interactive travel message needs a deliberate response
mode or a worked pattern for bookings, itinerary changes, documents, and trip
questions.

### Response Mode Selection

Choose the appropriate response mode based on the message type and action taken:

1. **React**: Quick acknowledgment without text (emoji only)
   - Use when: The action is simple and self-explanatory
   - Example: User uploads a boarding pass → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: The action needs a short confirmation with the key fact
   - Example: "Flight booked: SFO → NRT on March 15, confirmation ABC123"

3. **Follow-up**: Proactive question or suggestion
   - Use when: You notice a gap, can add context, or have a useful pre-trip observation
   - Example: "Your Tokyo trip starts in 3 days. Online check-in for your United flight opens tomorrow."

4. **Answer**: Substantive information in response to a direct question
   - Use when: The user asked for trip details or status
   - Example: User asks "What time does my flight land?" → Answer with arrival time and terminal

5. **React + Reply**: Combined emoji acknowledgment with message
   - Use when: You want immediate visual feedback plus substantive context
   - Example: React with ✅ then "Itinerary updated: departure moved from 10:15 to 13:40 due to delay."

### Complete Examples

#### Example 1: Flight Booking Confirmation Email (Affirm)

**Trigger**: Email reading "Your booking is confirmed: SFO → NRT, March 15, UA 837, PNR K9X4TZ"

**Actions**:
1. Create or match trip container for Tokyo / March 15–22
2. `record_booking(payload={"provider": "United Airlines", "type": "leg", "departure": "SFO", "arrival": "NRT", "departure_at": "2026-03-15T10:15:00-08:00", "pnr": "K9X4TZ", "source_message_id": "<email_id>"})`
3. `calendar_create_event(title="✈ SFO → NRT (UA 837)", start_at="2026-03-15T10:15:00-08:00", end_at="2026-03-16T14:30:00+09:00", description="PNR: K9X4TZ | Terminal 3")`
4. `calendar_create_event(title="Check-in: United SFO→NRT", start_at="2026-03-14T10:15:00-08:00", end_at="2026-03-14T10:30:00-08:00", description="Online check-in opens 24h before departure")`
5. `notify(channel="telegram", message="Flight booked: SFO → NRT on March 15 (UA 837, PNR K9X4TZ). Calendar blocks set. Check-in reminder: tomorrow.", intent="reply", request_context=...)`

---

#### Example 2: Flight Delay Notification (React + Reply)

**Trigger**: Email reading "Your United flight UA 837 on March 15 is delayed. New departure: 13:40"

**Actions**:
1. Find trip and leg by PNR or flight number
2. `update_itinerary(trip_id=<id>, patch={"leg_id": "<id>", "departure_at": "2026-03-15T13:40:00-08:00"}, reason="UA email: flight delay notification")`
3. `calendar_update_event(event_id=<flight_block_id>, start_at="2026-03-15T13:40:00-08:00", description="Delayed from 10:15 — updated per UA notification")`
4. `notify(channel="telegram", intent="react", emoji="⚠️", request_context=...)`
5. `notify(channel="telegram", message="UA 837 is delayed. New departure: 13:40 (was 10:15). Itinerary and calendar updated.", intent="reply", request_context=...)`

---

#### Example 3: Hotel Check-in Reminder (Follow-up, Scheduled Job)

**Trigger**: Scheduled job `upcoming-travel-check`, Tokyo trip starts tomorrow

**Actions**:
1. `upcoming_travel(within_days=2, include_pretrip_actions=true)`
2. Find upcoming check-in at Shinjuku Granbell Hotel
3. Check for missing pre-trip actions: boarding pass not yet attached
4. `notify(channel="telegram", message="Your Tokyo trip starts tomorrow!\n\n✈ UA 837 departs SFO at 13:40 (Terminal 3)\n🏨 Shinjuku Granbell Hotel check-in: March 16 at 15:00\n\nHeads up: boarding pass not yet attached — want to upload it?", intent="proactive", request_context=...)`

---

#### Example 4: Flight Departure Time Query (Answer)

**User message**: "What time does my Tokyo flight leave?"

**Actions**:
1. `list_trips(status="planned")` to find the Tokyo trip
2. `trip_summary(trip_id=<id>)` to retrieve legs
3. Find outbound flight leg with `departure_city="SFO"` or similar
4. `memory_recall(topic="Tokyo trip")` to enrich with any stored context
5. `notify(channel="telegram", message="Your Tokyo flight (UA 837) departs SFO at 13:40 on March 15 from Terminal 3. Arrives NRT March 16 at 14:30 local time. PNR: K9X4TZ.", intent="reply", request_context=...)`

---

#### Example 5: Boarding Pass Upload (React)

**User message**: [User sends boarding pass image or PDF]

**Actions**:
1. Find active or upcoming trip matching flight date/carrier
2. `add_document(trip_id=<id>, type="boarding_pass", blob_ref=<attachment_ref>, metadata={"flight": "UA 837", "gate": "B12"})`
3. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`

---

#### Example 6: Itinerary Rebooking (Follow-up)

**Trigger**: Email reading "Your United booking has been changed. New itinerary: SFO → NRT via ORD, March 15"

**Actions**:
1. Match trip by PNR or source message sender
2. `update_itinerary(trip_id=<id>, patch={"leg_id": "<original_leg_id>", "arrival": "ORD", "arrival_at": "2026-03-15T19:55:00-06:00"}, reason="UA rebooking email: new routing via ORD")`
3. `record_booking(payload={"provider": "United Airlines", "type": "leg", "departure": "ORD", "arrival": "NRT", "departure_at": "2026-03-15T22:10:00-06:00", "arrival_at": "2026-03-16T18:55:00+09:00", "source_message_id": "<email_id>"})`
4. Update calendar blocks: remove direct flight block, add SFO→ORD and ORD→NRT blocks
5. `notify(channel="telegram", message="Itinerary changed for your Tokyo trip.\n\nPreviously: SFO → NRT direct (10:15)\nNow: SFO → ORD (13:40) → NRT (arrives March 16 18:55)\n\nCalendar updated. Confirm this is correct?", intent="reply", request_context=...)`

---

#### Example 7: Trip Summary Request (Answer)

**User message**: "What's my trip summary for Tokyo?"

**Actions**:
1. `list_trips(status="planned")` to find the Tokyo trip
2. `trip_summary(trip_id=<id>, include_documents=true, include_timeline=true)`
3. Synthesize legs, accommodations, reservations, and alerts
4. `notify(channel="telegram", message="Tokyo Trip — March 15–22\n\n✈ Flights\n- SFO → NRT: Mar 15, 13:40 (UA 837, PNR K9X4TZ)\n- NRT → SFO: Mar 22, 11:00 (UA 838)\n\n🏨 Hotel\n- Shinjuku Granbell: Mar 16–22 (conf: HOTEL9X2)\n\n📄 Documents\n- Boarding pass: attached ✅\n- Travel insurance: attached ✅\n\n⚠️ No visa required (US passport)", intent="reply", request_context=...)`
