# Butlers

> **Scope:** One short profile per butler: purpose, where its identity and configuration live,
> and butler-specific contracts that have no other home.
> **Does NOT belong here:** schedules, modules, ports, or models (owned by
> `roster/<butler>/butler.toml` and the model catalog), identity and scope (owned by
> `roster/<butler>/MANIFESTO.md`), or module internals (see [Modules](../modules/index.md)).

| Butler | Purpose |
|--------|---------|
| [Switchboard](switchboard.md) | Single ingress, routing, and `notify()` relay |
| [General](general.md) | Catch-all collections and the routing fallback |
| [Relationship](relationship.md) | Personal CRM: contacts, dates, interactions, gifts |
| [Health](health.md) | Measurements, medications, conditions, symptoms, meals |
| [Messenger](messenger.md) | Outbound Telegram, email, and WhatsApp delivery |
| [Finance](finance.md) | Transactions, subscriptions, bills, and the SimpleFIN feed |
| [Travel](travel.md) | Trip containers built from booking and itinerary email |
| [Education](education.md) | Adaptive tutoring with spaced repetition |
| [Home](home.md) | Home Assistant comfort, scenes, energy, and actuation |
| [Chronicler](../../roster/chronicler/MANIFESTO.md) | Reconstructs lived past time from other butlers' timestamped evidence |
| [Lifestyle](../../roster/lifestyle/MANIFESTO.md) | Remembers taste: music, entertainment, food, hobbies, routines |
| [Concierge](../../roster/concierge/MANIFESTO.md) | System-plane staffer answering fleet status, spend, and session questions |
| [QA](../../roster/qa/MANIFESTO.md) | System-wide SRE staffer: error patrol, triage, investigation dispatch |
