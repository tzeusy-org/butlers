# The Messenger

## The Delivery Execution Plane

The Messenger is the single butler responsible for external user-channel delivery.

It turns routed delivery intents into concrete sends and replies on Telegram and Email, with consistent outcomes and strong operational boundaries.

## Why Messenger Exists

Outbound user communication should have one owner.

When every specialist butler sends messages directly, behavior drifts: inconsistent formatting, mixed retry logic, duplicated failure handling, and weak auditability. Messenger centralizes those concerns so specialists can focus on domain decisions while delivery stays reliable and policy-driven.

## What Messenger Guarantees

- One execution boundary for user-channel side effects.
- Identity-scoped channel surfaces for bot/user delivery flows.
- Deterministic handling for validation failures, provider failures, and overload.
- Consistent lineage from request origin to delivery outcome.

## Value to the Butler Ecosystem

Messenger is infrastructure, not a domain specialist.

By owning delivery once, it reduces system-wide complexity and makes outbound communication safer to reason about, test, and operate.
