# RFC 0017: Owner-routing safety and audit hardening — incident reconciliation

**Status:** Accepted
**Date:** 2026-04-29
**Owner-channel amendment:** 2026-09-13

---

## 1. Incident summary

On 2026-04-21 the relationship butler read a speculative question from the
owner ("would my ACME email be owner@example.com?") as a factual identity
claim and attached that work address to the owner. On 2026-04-26 a personal
notification resolved to the new work address and the email guard auto-approved
it because the recipient was the owner. Only an SMTP credential failure stopped
delivery. The fixes below are the durable contract that prevents a repeat.

---

## 2. Safety contract

### 2.1 Owner-email primacy gate (superseded by §2.7)

The original fix let the owner bypass apply only to the owner's primary email.
§2.7 replaced primacy with uniform owner-channel verification. Primacy remains
routing metadata; it is not an outbound authorization condition.

### 2.2 Context-aware send checks

`notify()` accepts `msg_context` (`personal`, `work`, `other`) and passes it to
`check_email_recipient` (`src/butlers/modules/approvals/email_guard.py`). For a
target without the owner bypass, an explicit sender context that conflicts with
the address's explicit context tag (`metadata->>'context'` on its
`relationship.entity_facts` triple) parks the send. An untagged address never
conflicts.

### 2.3 Owner-identity mutation gate

A write whose subject resolves to the owner entity MUST NOT land directly. The
central writer `relationship_assert_fact`
(`roster/relationship/tools/relationship_assert_fact.py`) parks it as a
`pending_action` for owner approval, and every tool that adds owner identity
facts (for example `channel_add`) inherits the gate. Only the trusted internal
sources named in `openspec/specs/relationship-facts/spec.md` are exempt.

### 2.4 Dashboard mutation audit

Every non-GET `/api/` request is audited by `DashboardAuditMiddleware`
(`src/butlers/api/dashboard_audit_middleware.py`), and sensitive handlers emit
explicit rows through `emit_dashboard_audit()` (`src/butlers/api/audit_emit.py`)
into `public.audit_log`, with sensitive field values redacted.

### 2.5 Evidence

`tests/reconciliation/test_incident_2026_04_21_replay.py` replays the incident
against §2.2 to §2.4 and §2.7.

### 2.6 Cross-schema owner lookup must preserve ambiguity

Schema-isolated butlers cannot read `relationship.entity_facts` directly, so
outbound gates use the owner-only `public.resolve_owner_triple` function rather
than receiving broader relationship-schema privileges. That fallback is an
authorization decision, not a general contact lookup: it MUST return an owner
match only when the supplied channel candidates resolve to exactly one live
entity across owner and non-owner facts.

Filtering to owner facts before checking uniqueness is unsafe. If the same
email address or Telegram identifier is attached to both the owner and an
external entity, owner-first filtering erases the collision and can create an
external-party bypass. The lookup therefore checks cross-entity ambiguity
first and returns no authorization on a collision. Every outbound channel uses
the same ambiguity-safe owner predicate. Primacy is retained as routing
metadata, but is not an outbound authorization condition. Lookup failure or
ambiguity falls through to standing rules and owner review.

### 2.7 Uniform owner-channel authorization amendment (2026-09-13)

An outbound communication receives the owner bypass exactly when the normalized
candidate identifier set resolves through active literal facts to one distinct
live, non-merged, non-deleted entity and that entity has the `owner` role.
Channel primacy is not an authorization condition. This applies uniformly to
email, Telegram, WhatsApp, and future communication integrations.

Unknown, external, ambiguous, inactive, merged, deleted, malformed, or
lookup-error identifiers receive no owner bypass and continue through
standing-rule or pending-approval handling. Context metadata may guide recipient
selection, but does not revoke the owner bypass from an exact qualifying owner
identifier.

---

## 3. Known limitations

### 3.1 Recipient resolution is context-blind

`relationship.entity_facts` carries no context column, so neither
`_resolve_entity_channel_identifier` (the `contact_id` path) nor
`_resolve_default_notify_recipient` (the explicit `recipient` path) prefers a
matching-context address. Context enforcement happens only in the email
guard's mismatch check (§2.2). An exact, uniquely verified owner address
bypasses approval regardless of context metadata under §2.7.

### 3.2 No butler-level default `msg_context`

Butler-domain context inference (relationship defaults to `personal`, finance
requires an explicit value) is not implemented. RFC 0004 §"Default context inference" records that
callers must supply `msg_context` explicitly. This is an accepted limitation.
