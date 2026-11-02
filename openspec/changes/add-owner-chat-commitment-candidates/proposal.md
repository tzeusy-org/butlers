## Why

The owner makes promises in their own WhatsApp and Telegram chats ("I'll send you the deck
Friday"). Butlers already reads those chats, and the commitment ledger can track the promise, but
only the routed explicit-owner extraction of REQ-commitment-lifecycle-007 writes commitments, and
it sees only what the owner tells Butlers. The owner released slice S1 of the promise radar
(bu-4ap4ap, run 20 move 11, bead bu-jv1moa.11): verified owner-authored one-to-one chat promises
become tainted candidates, and one signed Telegram card lets the owner Track, mark Done, or
Ignore each. bu-jv1moa.11 cannot be dispatched until the source, storage, transport, lifecycle
and cap contract exists. This change supplies it, including the owner's card-cap decision.

This is a spec-first change. It authorizes no implementation, migration, deployment or live
message.

## What Changes

- ADD to `commitment-lifecycle` (REQ-commitment-lifecycle-011 to -017): candidate production,
  idempotence and duplicate suppression, the card through the signed confirm transport, Track-only
  promotion with exact Done and Ignore semantics, 14-day silent lapse with content minimization,
  coexistence with the routed REQ-007 path, and the adopted no-cap rule for S1 cards.
- ADD to `connector-base-spec` (REQ-connector-base-spec-013): explicit per-message `is_from_me`
  and `forwarded` transport facts on user-client conversation history entries.
- ADD to `butler-switchboard` (REQ-butler-switchboard-011): a fixed, authenticated, read-only
  owner-chat span projection that Relationship reads with its own cursor, so no cross-butler SQL
  and no message-text copy outside the Switchboard inbox is introduced.
- `design.md` gives the ingress-to-Relationship source map, schema, checkpoint and authority
  ownership, the full Track/Done/Ignore/race/replay/expiry matrix, the decisions taken and the
  alternatives rejected.
- Records the owner decision of 2026-11-02 (bu-jv1moa.43, option C): S1 cards have no
  per-counterparty daily cap; the cap is revisited when S2 is specified
  (REQ-commitment-lifecycle-017; design.md, "Owner decision: card cap").

## Non-Goals

- No implementation, code, migration, test or runtime activation. bu-jv1moa.11 implements S1
  after this change merges.
- No change to REQ-commitment-lifecycle-007 or -008. The routed path keeps auto-creating.
- No counterparty-authored candidates (S2), Gmail (S3) or nudge drafts (S4).
- No send over the owner's personal WhatsApp or Telegram account (bu-bup68v); the card goes
  through the Telegram bot only.
- No new callback, HMAC or token scheme; the card reuses the `cfm1` signed confirm transport
  (bu-ow5a4, `notify-confirm-interaction`). No `apr1` token or confirm answer becomes approval
  authority.
- No model-backed extraction; `detect_commitment` stays the only detector.
- No dashboard surface for candidates.

## Capabilities

### Modified Capabilities

- `commitment-lifecycle`: ADDED requirements only.
- `connector-base-spec`: ADDED requirement only.
- `butler-switchboard`: ADDED requirement only.

No MODIFIED block is added, so there is no overwrite risk against
`generalist-lead-focused-delegation` or `add-meeting-debrief`, which also touch
`commitment-lifecycle`.

## Impact

- Code surfaces at implementation: `src/butlers/connectors/whatsapp_user_client.py`,
  `src/butlers/connectors/telegram_user_client.py` (transport facts);
  a Switchboard projection in `roster/switchboard/tools/`; a Relationship migration, reducer job
  and confirm sink under `roster/relationship/`; a `ConfirmSource` registration in the trusted
  signed-confirm composition (`src/butlers/core/confirm_authority.py`).
- Activation: card emission depends on the signed-confirm composition, which the canonical
  RFC 0038 switch installs (bu-atc3xi). The reducer reads new spans only while that composition is
  installed, so no candidate content exists without a surface.
- Documentation at implementation: `docs/runtime/signed-confirms.md` (new registered source),
  `roster/relationship/AGENTS.md`, connector docs.
- Affected RFCs: none amended.
- Rollback: close this PR (draft), or revert it (after merge, before implementation).
