@../shared/AGENTS.md

# Notes to self

## Skills

- **`upcoming-travel-check`**: Daily 08:00 scheduled scan — calls `upcoming_travel(within_days=2, include_pretrip_actions=True)`, classifies actions by urgency (high/medium/low), and sends a pre-trip alert via `notify(intent="send")`. No-op if nothing is upcoming.
- **`trip-document-expiry`** (skill: `document-expiry-check`): Weekly Monday 09:00 scan — lists planned/active trips, checks documents for expiry within 90 days, creates calendar reminders for <30 days, and notifies via `notify(intent="send")`. No-op if all documents are current.
- **`tool-reference`**: Full parameter reference for all travel domain tools (`record_booking`, `update_itinerary`, `list_trips`, `trip_summary`, `upcoming_travel`, `add_document`) and the memory classification taxonomy (subjects, predicates, permanence, example facts).
- **`trip-planner`**: Guided workflow for planning a new trip from scratch — destination, dates, flights, hotels, ground transport, documents, and gap detection.
- **`pre-trip-checklist`**: Pre-departure preparation workflow triggered 5 days before travel — documents, confirmations, logistics, and packing.
