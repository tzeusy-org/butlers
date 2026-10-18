## Tasks

Spec only. No code, migration, provider call or live action is part of this change.

### 1. Draft and adopt

- [x] 1.1 Draft proposal, design and the `relationship-turn-status` and
      `connector-owner-direction-evidence` deltas under posture A.
- [ ] 1.2 Independent review of the exact head reports no blocking findings.
- [ ] 1.3 Owner adopts or declines. On adoption bu-h40h2b.14 unblocks; on decline close
      bu-h40h2b.14 as declined.

### 2. Verify the unknowns before implementation

- [ ] 2.1 Retention of `switchboard.message_inbox` (partitioned; `data-retention.md` does not list it):
      it bounds the evidence window in REQ-004 and REQ-007.
- [ ] 2.2 Whether `interaction_sync` already skips inbound rows with `control.automated_kind`; REQ-002
      requires it.
- [ ] 2.3 Whether per-message timestamps inside batch envelopes are parseable from `message_inbox`
      for WhatsApp and Telegram user client, so a same-day order could be established from stored data
      rather than only from `occurred_at`.
- [ ] 2.4 Which tool or route surfaces the projection (an MCP tool, the entity detail API, both) and
      how `basis` is shaped; names to be fixed by the implementation bead, not this change.
- [ ] 2.5 Whether the Gmail on-demand read (bu-kqme7f.5) will expose a per-person SENT query within its
      50-thread cap, and under what grant, for a later email-outbound upgrade.
- [ ] 2.6 How a person's resolved identities are enumerated across `contact_entity_map` and identity
      facts, and how a "merge-pending" identity is recognized (`merge_review`).
- [ ] 2.7 The canonical event key used to count an event once across a person's identities (channel,
      endpoint, conversation, message or day); confirm it is derivable from `message_inbox` rows.

### 3. Follow-on (separate beads, not this change)

- [ ] 3.1 Read projection and API, with synthetic ordered inbound and outbound fixtures: direction
      changes, duplicate idempotence, late and retracted evidence, group and auto-reply exclusion,
      content-blind output, recomputation after merge.
- [ ] 3.2 UI reads: state chips pairing text and icon, `not_tracked`, `no_signal` and `unavailable`
      distinct to sight and screen readers.
- [ ] 3.3 Circles by canonical person (slice D) and occasion candidates (slice E) under their own
      owners; not specified here.
