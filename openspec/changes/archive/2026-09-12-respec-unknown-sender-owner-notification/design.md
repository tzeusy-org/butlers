## Context

The archived `contacts-identity` requirement notified the owner when a
temporary contact row was created. RFC 0004 and the current identity helper
instead make `public.entities` plus active `relationship.entity_facts` the
identity model: an unresolved sender is represented by a transitory entity
whose `metadata.unidentified` flag makes it reviewable.

The helper creates only that entity, but its inactive owner-notification branch
still builds a legacy contacts target. Its best-effort `butler_state` read/write
handling neither persists failures nor atomically claims a sender before
delivery, so it is not durable or race-safe deduplication. It is not
fleet-active: `wire_pipelines()` leaves `enable_identity_resolution` at its
default `False` and supplies no `notify_owner_fn`. This change records the
desired contract and that runtime gap without adding a second implementation
track.

## Goals / Non-Goals

**Goals:**

- Make the owner-approved unknown-sender notification an entity-first
  requirement with explicit one-time, privacy, and route-continuity semantics.
- Remove the live Switchboard spec's dependency on the archived
  `contacts-identity` requirement for this path.
- Make the missing fleet activation visible to implementers and reviewers.

**Non-Goals:**

- Reintroducing `public.contacts`, `public.contact_info`, `contact_id`, or a
  table-backed disambiguation queue.
- Migrations, frontend changes, route redesign, or a broad repair of other
  legacy contact wording.
- Enabling identity resolution or wiring owner delivery in this change.

## Decisions

### The entity lifecycle owns the notification contract

`entity-identity` owns the lifecycle of a transitory entity and therefore
owns the owner-surfacing requirement. `switchboard-identity` only states that
an unknown-sender miss invokes that entity-first flow. This keeps the behavior
anchored to the canonical identity object rather than to the legacy helper
name `create_temp_contact()`.

### Notification is one best-effort owner-facing attempt, not a routing gate

The intended notification is deduplicated per newly surfaced sender identity
and must not repeat for later messages that resolve to the same entity.
Delivery failure must not block routing or expose raw inbound message content;
a failed attempt is still sealed against notification storms. The notice must
identify the sender only with the safe display label and source channel needed
to review the transitory entity, and must direct the owner to the
unidentified-entity review flow rather than a contact-table record. The
inactive helper does not yet provide this durable, race-safe guarantee.

### Runtime activation stays separate

Activating this contract requires a focused runtime change to enable identity
resolution in the production Switchboard pipeline, provide the standard
owner-notification delivery boundary, replace the legacy contacts target with
the Unidentified Entities review flow, and establish durable, race-safe
deduplication before delivery. Combining that wiring, delivery-policy, and
observability work with this archival/spec reconciliation would broaden the
slice and hide its independent review surface.

## Risks / Trade-offs

- **[Normative contract precedes active fleet wiring]** → The developer
  documentation states the gap explicitly, and a future runtime change must
  prove the exact end-to-end path before claiming delivery is live.
- **[Legacy code names can mislead reviewers]** → The requirements describe
  entities and entity review, not helper or table names.
- **[Unknown senders are untrusted]** → The notification contains no inbound
  message body and does not grant the sender any owner role or approval.
