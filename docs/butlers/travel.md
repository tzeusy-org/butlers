# Travel Butler

> **Purpose:** Travel logistics and itinerary intelligence specialist that transforms booking confirmations and travel emails into a structured, queryable trip container model.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

The Travel Butler transforms the chaos of travel booking emails into organized certainty. It ingests booking confirmations, itinerary updates, and travel documents from email, then normalizes them into a trip-level container model -- trips contain legs (flights, trains, buses, ferries), accommodations (hotels, Airbnbs, hostels), reservations (car rentals, restaurants, activities, tours), and documents (boarding passes, visas, insurance, receipts). Changes across providers are correlated and surfaced as actionable travel state.

The butler proactively monitors upcoming departures, check-in windows, connection times, and document expiry dates, alerting the user with enough lead time to act.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41106 |
| **Schema** | `travel` |
| **Modules** | email, calendar, memory, travel |
| **Runtime** | codex (gpt-5.1) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `upcoming-travel-check` | `0 0 * * *` | Daily pre-trip scan (08:00 SGT). Check for departures and check-ins within 48 hours. Surface missing boarding passes, pending online check-in windows, and tight layover warnings. Delivered via Telegram. |
| `trip-document-expiry` | `0 9 * * 1` | Weekly Monday scan. Check all stored travel documents (passports, visas, insurance) for expiry within 90 days. Create calendar reminders for documents expiring within 30 days. |

## Tools

**Trip Management**
- `record_booking` -- Parse and persist a booking confirmation or update into the trip container. Extracts structured fields (PNR, confirmation number, departure/arrival times, seat, terminal) and links the entity to the correct trip. Returns `trip_id`, `entity_type`, `entity_id`, and deduplication status.
- `update_itinerary` -- Apply itinerary changes (time changes, cancellations, seat/gate reassignments, rebookings). Always preserves prior values in `metadata.prior_values` for audit history.
- `list_trips` -- Query trip containers by lifecycle status (planned, active, completed, cancelled) and date window.
- `trip_summary` -- Full normalized trip timeline with all linked legs, accommodations, reservations, documents, and alerts.
- `upcoming_travel` -- Surface upcoming departures and check-ins within a configurable window, with urgency-ranked pre-trip actions.
- `add_document` -- Attach a travel document (boarding pass, visa, insurance, receipt) to an existing trip.

**Calendar** -- Blocks travel time windows (departure to arrival), creates check-in reminders 24 hours before departure, and adds document expiry warnings 30 days before visa or insurance expiry.

## Persistence

The travel schema contains five domain tables:

- **`travel.trips`** -- Trip-level container with name, destination, dates, and lifecycle status.
- **`travel.legs`** -- Transport legs (flight, train, bus, ferry) with carrier, departure/arrival details, PNR, and seat.
- **`travel.accommodations`** -- Lodging bookings (hotel, airbnb, hostel) with check-in/check-out times and confirmation numbers.
- **`travel.reservations`** -- Non-transport, non-lodging reservations (car rentals, restaurants, activities, tours).
- **`travel.documents`** -- Travel documents and receipts with blob references and expiry dates.

All entities link to a trip via `trip_id`. Status transitions follow `planned -> active -> completed` with direct cancellation allowed from planned or active states.

## Key Behaviors

**Trip Container Model.** Every booking entity must be linked to a `trip_id`. Floating bookings are not allowed. If no matching trip exists, the butler creates one first, then attaches the entity.

**Change Detection.** When an email arrives with signals like "gate change", "trip update", or "delay notification", the butler defaults to `update_itinerary` rather than `record_booking`. It preserves what changed and surfaces the delta.

**PNR and Confirmation Handling.** These are treated as correlation keys, not global uniqueness keys. Providers can reuse PNR formats across accounts, so they are always paired with carrier or provider context for accurate deduplication.

**Switchboard Classification Signals.** Routing uses sender-domain signals (airlines: delta.com, united.com; lodging: booking.com, airbnb.com; OTAs: expedia.com, kayak.com), subject-pattern signals ("Booking confirmation", "E-ticket", "Check-in reminder", "Gate change"), and body cues (PNR formats, paired departure/arrival timestamps with airport codes).

## Interaction Patterns

**Email-driven ingestion.** Booking confirmations and itinerary updates are routed from Switchboard, parsed for structured data, and recorded in the trip container without requiring user interaction. Significant changes trigger a Telegram notification.

**Pre-trip alerts.** The daily upcoming-travel-check surfaces imminent departures and check-ins with actionable details: terminal, gate, PNR, missing boarding passes, and pending online check-in windows.

**Direct queries.** Users ask "What time does my Tokyo flight leave?" or "What's my trip summary?" and receive data-backed answers from the trip container model.

**Document management.** Users upload boarding passes, visas, and insurance documents which are attached to the relevant trip for tracking and expiry monitoring.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes travel-related emails and messages here
- [Finance Butler](finance.md) -- handles expense tracking; Travel Butler stores receipts as documents but does not do accounting
- [Messenger Butler](messenger.md) -- delivers pre-trip alerts and itinerary change notifications
