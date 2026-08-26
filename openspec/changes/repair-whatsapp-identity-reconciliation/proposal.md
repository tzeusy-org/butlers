## Why

[Observed] The healthy WhatsApp user connector persists messages, but routing-time identity
resolution receives the transport channel `whatsapp_user_client` while the canonical phone-aware
resolver only recognizes `whatsapp_jid`. Known people are therefore surfaced as unidentified raw
JID/LID entities, and buffered conversation decomposition can promote those transport identifiers
into fact-storage entity names.

This change restores the project motif that transport identifiers remain structured machine data
while every person mentioned to downstream butlers carries a deterministic canonical entity anchor.

## What Changes

- Translate the WhatsApp transport channel to the canonical `whatsapp_jid` identity type without
  changing persisted ingestion-channel semantics.
- Normalize device-qualified JIDs and LIDs before any sender representation becomes LLM-visible.
- Resolve every distinct speaker in a buffered WhatsApp conversation and preserve its entity UUID
  through decomposition excerpts and routed conceptual messages.
- Reject fact-storage attempts to create entities whose names are WhatsApp transport identifiers.
- Add a content-blind reconciliation command that defaults to dry-run, requires an exact plan digest
  for apply, and can collapse only unambiguous, reference-free transitory shells.
- Add structured, identifier-blind observability and regression coverage across connector,
  Switchboard, memory, relationship, and operator paths.

Explicitly out of scope:

- WhatsApp address-book synchronization or trusting PushName as canonical identity.
- Automatic reconciliation during migration, startup, deployment, or polling.
- Merging ambiguous, unmatched, referenced, owner, or system entities.
- Rewriting historic ingestion payloads or broadly redesigning every existing merge path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `switchboard-identity`: Define canonical WhatsApp transport-to-identity translation and
  deterministic per-speaker resolution for buffered conversations.
- `connector-base-spec`: Require every LLM-facing sender representation to use normalized structured
  identity and a non-identifier display label.
- `conversation-decomposition`: Preserve `sender_identity` and `sender_entity_id` through conceptual
  excerpts.
- `entity-identity`: Prohibit fact-storage transport-identifier entities when a structured sender
  anchor exists and define the guarded reconciliation workflow.

## Impact

- Connector normalization: `src/butlers/connectors/whatsapp_user_client.py`.
- Switchboard buffering, identity resolution, decomposition, and routing context:
  `src/butlers/switchboard_wiring.py` and `src/butlers/modules/pipeline.py`.
- Identity and fact assertion: `src/butlers/identity.py` and
  `roster/relationship/tools/relationship_assert_fact.py`.
- Memory tool boundary and fact-extraction guidance: `src/butlers/modules/memory/`,
  `roster/shared/skills/butler-memory/`, and the Relationship fact-extraction skill.
- Relationship merge audit service and the new repository-owned reconciliation command.
- No dependency, credential schema, database migration, frontend, or public API response change is
  expected.

## Feature Funnel

Size: medium. Baseline: `0b0d87a1156f300021aebfc6aabcc5f5bdd2d63e`.

- G1 Motif [Observed]: known WhatsApp people must reuse canonical identities; machine transport
  identifiers must never become person names; genuinely unknown speakers still need reviewable
  entity anchors.
- G2 Doctrine [Observed, aligned]: connectors normalize transport while deterministic infrastructure
  owns identity; intelligence remains outside the daemon (`about/heart-and-soul/architecture.md`).
- G3 Topology [Observed]: connector normalization feeds Switchboard identity/decomposition, then
  structured route context feeds domain memory tools; reconciliation is an explicit operator path.
- G4 Design [Observed]:
  `docs/superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md`.
- G5 Spec: four modified capability deltas in this changeset; no new capability.
- G6 Bar [Observed]: TDD, content-blind logs, exact-digest apply authorization, transactional
  revalidation, same-change docs, targeted-to-broad verification, and independent review.
- Open questions: none.
- Sign-off: owner approved the design and end-to-end implementation on 2026-08-24.
