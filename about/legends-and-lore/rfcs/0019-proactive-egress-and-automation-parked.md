# RFC 0019: Proactive Egress and Automation (Parked / Rejected)

**Status:** Calendar-based auto-responses **Rejected** (owner, 2026-06-14); event-driven automation rule engine **Parked** — doctrine decision deferred
**Date:** 2026-06-14

---

## Summary

This RFC records the disposition of two doctrine-gated egress capabilities and
the open questions any future egress spec must answer. Both capabilities involve
the daemon **acting or messaging on the owner's behalf without per-event
review**.

**Dispositions (2026-06-14):**

- **Calendar-based auto-responses — REJECTED.** The owner explicitly declined this
  idea; it is scrapped, not backlogged. Recorded below so it is not re-proposed.
- **Event-driven automation rule engine — PARKED.** Considered over-engineered for
  now; held pending an explicit doctrine decision before any spec or implementation.

Nothing else in this RFC is accepted. The parked item is blocked on an explicit
doctrine decision by the owner before any work begins.

**Scope guard — Owner Attention Policy consolidation.** Consolidating the
existing owner-attention quiet-window authority is policy-data repair only. It
does not un-park automated egress, add broker catch-up/wake/cron/digest
behavior, or create a new delivery authority.

### Why these items are parked

The Butlers doctrine draws a hard line around autonomous action with real-world
consequences:

- **Per-event human review is the default for egress.** `about/heart-and-soul/security.md`
  (Approval Gates, ~L75-94) names "sending messages on behalf of the owner
  (email, Telegram)" and "any action with real-world consequences that cannot be
  undone" as use cases that must pause for explicit owner confirmation. Approval
  gates "must never be bypassable by the LLM session," and "approval timeouts
  must result in denial, not silent approval." A capability that sends without
  per-event review inverts this default and therefore needs a doctrine decision,
  not just a spec.

- **Brokered, never direct — RFC 0011 precedent.** RFC 0011 (Proactive Insight
  Delivery) establishes the doctrine for proactive owner-facing output: butlers
  **propose** candidates and **compete** for delivery slots; they **never deliver
  directly**. A central broker enforces a hard global budget, per-key cooldowns,
  and a one-way adaptive ratchet — anti-spam is *structural*, not advisory.
  Crucially, even RFC 0011 only ever delivers insights *to the owner*. The parked
  capabilities below go further: they would send *to third parties* or *mutate
  external state* on the owner's behalf. That is a strictly larger trust
  surface than RFC 0011 ever opened, and RFC 0011's brokered-not-direct
  principle is the floor any such design must clear, not the ceiling.

- **Butlers is not a general automation platform.** `about/heart-and-soul/vision.md`
  ("What Butlers Is Not", ~L31-49) frames Butlers as a single-user personal
  assistant that acts autonomously *on its own cron schedules and manifesto-scoped
  domains* — not as a user-programmable rule engine for arbitrary
  trigger→action chains. Capability B below directly tensions with this framing.

- **The daemon is deterministic infrastructure; judgment lives in ephemeral
  sessions.** `about/heart-and-soul/architecture.md` (~L80-84) and Non-Negotiable
  Rule 4 hold that the daemon "manages state, runs migrations, enforces
  schedules, and registers tools" and must be "testable, debuggable, and
  predictable" — it may not autonomously decide to take consequential action.
  Any auto-action design must keep the *decision* in a reviewable place, not bury
  it in daemon control flow.

- **Manifesto-gating (Non-Negotiable Rule 6).** Every capability must be deeply
  aligned with the owning butler's governing manifesto; a capability that
  contradicts it requires a formal amendment. The parked items have no manifesto
  home yet — deciding *which* butler may auto-act, and amending its manifesto to
  say so, is part of the doctrine decision.

---

## Rejected: Calendar-based Auto-Responses

**Origin:** plan §3.5 (Tier 2). **Disposition:** **REJECTED by the owner, 2026-06-14
— scrapped entirely, do not re-propose.**

**What it was.** When the owner was marked busy on their calendar, the system would
auto-reply to inbound Telegram messages with a brief status (e.g. "In a meeting,
will respond later") without per-message owner review, with template-only replies,
an allowlist, no schedule-detail leakage, and urgency escalation.

**Why rejected.** The owner does not want automatic replies sent on their behalf.
Beyond preference, the idea sits on the wrong side of the doctrine line: it sends
messages to third parties without per-event review (`about/heart-and-soul/security.md`
Approval Gates), and there is no sanctioned mechanism for third-party-facing
auto-replies (RFC 0011's brokered model covers only *owner-facing* delivery). This
is a closed decision, recorded here so the idea is not reintroduced as a backlog
item.

---

## Parked Capability: Event-Driven Automation Rule Engine

**Origin:** plan §2.1 (Tier 3). **Owning component:** Switchboard (rule
evaluation/dispatch) + domain butlers (action execution).

**Idea.** An owner-defined rule engine: trigger → condition → action chains,
stored durably and evaluated when matching events arrive. Examples from the plan:
"when a bill email arrives, extract amount + due date, create a calendar reminder,
file the email"; "when a health measurement is abnormal, alert + schedule a
follow-up"; "when a new contact is added, check for social profiles." Two flavors
were sketched: **template rules** (predefined trigger→action templates,
deterministic, no LLM at eval time) and **LLM-planned rules** (owner describes
intent in natural language, an LLM decomposes it into a stored plan at *creation*
time, executed deterministically on trigger).

**Doctrine tension.** This is the item closest to the line. A general
trigger→action engine is, definitionally, the "general automation platform" that
vision.md says Butlers is **not**. It also risks putting consequential
*decisions* into daemon-side rule evaluation, tensioning Non-Negotiable Rule 4
(the daemon is deterministic infrastructure; it does not autonomously decide to
act). The plan's own mitigations (approval gates on high-risk actions, dry-run
mode, execution audit log) reduce but do not resolve the framing question:
*should Butlers offer user-programmable automation at all*, or only fixed,
manifesto-scoped behaviors owned by individual butlers?

**Open questions (recorded).**

- **Rule language: structured JSONB vs. natural language?** JSONB is
  deterministic, fast, and debuggable but rigid; NL is flexible but requires an
  LLM at rule-creation time (and risks ambiguity in what was authorized). The
  plan leaned toward NL-at-creation, deterministic-at-execution as a compromise.
- **Where does the decision live?** If rules execute in the Switchboard triage
  pipeline, the consequential decision is daemon-side — directly against Rule 4
  unless every action routes back through an approval gate or an ephemeral
  session.
- **How is a "rule" bounded by a manifesto?** A cross-butler rule has no single
  manifesto home; Rule 6 has no obvious answer here yet.

**Note on the safe subset.** The plan's §2.2 "bill email processing" is a
*specific, manifesto-scoped* behavior owned by the Finance butler (extract →
track → internal reminder), not a generic rule. That narrow, fixed behavior is
**not** parked by this RFC and may be specced on its own merits under Finance's
manifesto — it is the general engine, not the specific behavior, that is gated.

---

## Open Questions

Several bear on the parked capabilities above; others are general egress
infrastructure questions that any future egress spec must answer.

1. **PDF vs. rich HTML rendering.** Should document generation target PDF
   (portable, printable), rich HTML (interactive, linkable), or both behind a
   format parameter? (Bears on the document-renderer module.)
2. **File storage: MinIO vs. cloud storage.** Is MinIO the right long-term file
   store, or should egress integrate with the owner's cloud storage (Google
   Drive, Dropbox)? **Partly answered by RFC 0016 (S3-blob-storage contract)**,
   which establishes the blob-storage interface; the open part is whether to add
   owner-cloud backends behind it.
3. **Automation rule language: JSONB vs. natural language?** (See Capability B —
   this is the central design fork for the rule engine.)
4. **Cross-butler orchestration layer.** Multi-butler aggregations (meeting-prep
   briefs, tax packages) need data from several butlers. Reuse existing
   Switchboard routing, or introduce a dedicated orchestration layer? Any answer
   must respect Non-Negotiable Rule 3 (inter-butler comms are MCP-only through
   the Switchboard).
5. **Channel-expansion scope.** WhatsApp and Discord connectors are specified but
   unbuilt. Should egress enhancements target only Telegram + email now, or be
   designed for N channels from the start?
6. **Standing-rule guardrails.** How broad may standing approval rules get?
   "Auto-approve all Telegram sends" is convenient and dangerous. What scoping
   (per-contact, time-bounded, action-typed) prevents overly permissive rules?
   This is the guardrail question underlying the parked rule engine.

---

## Future Application Ideas

The per-butler egress applications that depend on the parked capabilities
(payment initiation, booking changes, delivery ordering, external contact
enrichment, face-recognition media tagging) live in the
[Ideas Ledger](../ideas-ledger.md#egress-owner-delegated-actions-rfc-0019), each
with its unpark condition.
