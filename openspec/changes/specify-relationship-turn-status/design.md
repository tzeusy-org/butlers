## Context

Every claim is read from main `7cfd3b301`. `[Observed]` means read in code or spec at the cited place.
`[Unknown]` has a verify task in tasks.md. Nothing here ran a provider call.

## What exists today

- `[Observed]` Interaction evidence is `relationship.entity_facts` rows with predicate `interaction_%`
  and `metadata->>'direction'` of `incoming`, `outgoing` or `mutual` (`dunbar.py:279-347`). Rows
  written by the deterministic `interaction_sync` job carry a server attestation
  (`stale_contacts.py:39`).
- `[Observed]` `interaction_sync` scans `switchboard.message_inbox` where `direction = 'inbound'` on
  `email`, `telegram_user_client`, `whatsapp_user_client` (`relationship_jobs.py:867-871,1114`),
  groups by chat and day, skips `interaction_eligible = false`, skips groups over 20 participants, and
  excludes the owner via `request_context.owner_sender_identity` or the owner role. If the owner sent
  at least one message in the chat that day, each other contact gets an `incoming` and an `outgoing`
  fact; otherwise only `incoming`. The outgoing fact uses a +12 hour offset so both coexist
  (`relationship_jobs.py:873`, docstring at `:917-940`). So direction within a day is a convention,
  not an order.
- `[Observed]` Gmail: `_fetch_sent_message_ids` lists `in:sent newer_than:{GMAIL_SENT_LOOKBACK_DAYS}d`,
  reads each message with `format=metadata` and `metadataHeaders=Message-ID`, and stores the IDs in a
  set assigned to `PolicyTierAssigner.sent_message_ids` (`gmail.py:711,760-768,784`) so replies to the owner's
  sent mail are tiered. No recipient, date or thread is kept.
- `[Observed]` WhatsApp: the bridge tags each message `raw.is_from_me`; `_project_batch_senders`
  sets `owner_sender_id` for the batch from it (`whatsapp_user_client.py:1805-1830`). Telegram user
  client tags the owner in batch participants (`telegram_user_client.py:1156`).
- `[Observed]` `connectors.owner_outbound_events` records owner-sent messages with timestamp and
  channel only, a hashed dedup key, and no counterpart (`owner_outbound_events.py`, privacy contract in
  its docstring).
- `[Observed]` Gmail marks automated replies and bounces with `control.automated_kind` (connector-gmail
  spec, Tier 2 `bounce` or `auto_reply`).
- `[Observed]` Stale-contact cadence: a contact with no producer-attested interaction is `unmeasurable`
  (`dunbar.py:1029`, REQ-butler-relationship-001).
- `[Observed]` Merge repoints active `entity_facts` with lifecycle intact and tombstones the loser
  (`entity_merge.py` docstring); retraction sets `validity = 'retracted'`
  (`relationship_assert_fact.py:~2203`).
- `[Observed]` bu-kqme7f.5 (open) would give the lead on-demand `gmail_search` and `gmail_get_thread`
  over the owner's Gmail after the RFC 0038 switch. It is the only designed route to per-person email
  outbound evidence without storing anything.

## Consequences that shape the spec

- The 7-day `in:sent` set cannot give a turn. Posture A's "derive from the 7-day metadata" holds only
  for what it contains, which is nothing person-specific. Email outbound is therefore `not_tracked`
  until the on-demand read exists and is granted (REQ-002).
- An email-only person is `unknown`, not `owner`: the owner may have replied unseen (REQ-004).
- Day-resolution interaction facts cannot order a same-day exchange, so those are `unknown` unless a
  `source_stated` time exists (REQ-004). This is where #4686's event clock helps.

## Decisions

[decision] A four-state per-direction vocabulary (`observed`, `not_tracked`, `no_signal`,
`unavailable`) next to, not replacing, `unmeasurable`. Cost: two vocabularies. Reason: cadence
`unmeasurable` is fail-closed authority for nudges and must not move. Reversible: yes.

[decision] Turn is determinate only when both directions are tracked on the newest event's channel
and nothing newer sits on a channel that cannot see one direction. Conservative on purpose; the worst
case is `unknown`, never a wrong `owner`.

[decision] Scope split. REQ-006 (no outreach authority), REQ-007 (privacy) and REQ-008 (out-of-scope
guard), and connector REQ-002 and REQ-003, are `v1-mandatory` because they are guardrails that apply
as soon as anything reads this evidence. The projection, evidence rules, taxonomy, ordering and
identity rules are `v1-reserved`: specified for adoption, built when the owner schedules the move.

[decision] Two new capabilities rather than edits to `passive-interaction-sync` or
`butler-relationship`, to avoid overwrite collisions with changes that already modify those blocks and
to keep the turn contract separable.

## Open questions for the owner

1. May owner-entered manual interactions (dashboard, attested `owner`) count as outbound turn
   evidence? Default here: they do not, because they carry no connector provenance.
2. Is `unknown` for email-only people acceptable for the first release, with the on-demand Gmail read
   as the later improvement?
3. How should the evidence window be shown when the oldest source retention is unknown?
