## Context

See `proposal.md` for motivation and
`docs/superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md` for the approved design
record. The critical current constraints are:

- `source_channel=whatsapp_user_client` is a transport, policy, replay, and telemetry key that cannot
  be renamed to fix identity.
- WhatsApp LID-to-phone normalization exists for `sender.participants` but not for normalized text or
  conversation history.
- conversation decomposition reduces speaker information to an LLM-selected excerpt today;
- fact storage already accepts a routed sender entity from runtime context, but group batches need a
  distinct entity anchor per speaker;
- existing merge paths have different reference coverage, so automated cleanup is safe only for a
  source proven to be an empty transitory shell.

## Goals / Non-Goals

**Goals:**

- Establish one canonical transport-to-identity translation without changing persisted transport
  semantics.
- Preserve a deterministic entity UUID per decomposed speaker.
- Keep raw provider identifiers out of LLM-visible names and content-blind logs.
- Provide a reviewed-plan cleanup path with transactional drift protection.

**Non-Goals:**

- Import a WhatsApp address book or trust PushName.
- Make identity decisions inside the connector or an LLM.
- Auto-run cleanup or broaden it to referenced entities.
- Repair every pre-existing general merge-path discrepancy.

## Decisions

### Decision 1: Translate identity type at one shared boundary

The identity layer will canonicalize `whatsapp_user_client` to `whatsapp_jid` before lookup,
phone fallback, storage normalization, and post-resolution fact assertion. Callers continue carrying
the original source channel.

Alternatives rejected:

- changing the ingest source channel would break policy, replay, metrics, and historical queries;
- translating only in `MessagePipeline` would leave direct resolver and assertion callers divergent.

### Decision 2: Preserve structured history until speaker resolution completes

The connector will emit normalized `sender_identity` separately from a neutral `sender` label. The
decomposition path will keep structured messages, resolve distinct identities once, enrich each
message with `sender_entity_id`, and only then format untrusted text for signal extraction. The
primary routing preamble reuses the same resolution map.

Mapped LIDs become device-free phone JIDs. Unmapped LIDs remain available only as structured internal
reservation keys and are never rendered as labels.

Alternatives rejected:

- prompt-only instructions remain model-dependent;
- PushName is spoofable and non-unique;
- using only the top-level sender UUID misattributes group participants.

### Decision 3: Make excerpt identity additive and authoritative

Conceptual excerpts add `sender_identity` and `sender_entity_id` while retaining the existing fields.
Signal extraction chooses relevant messages but cannot synthesize or replace identity anchors or
select a direct target tool; the pipeline joins model-selected message IDs back to authoritative
input records and builds the target's standard `route.v1` / `route.execute` session envelope before
fan-out. The conceptual message travels in `input.context`, where the target runtime receives it.
The existing code-authoritative calendar proposal translation remains the explicit exception.

This join prevents the runtime from fabricating UUIDs and ensures a message duplicated across concepts
keeps the same speaker entity.

### Decision 4: Reject transport-shaped fact-storage creation

The memory MCP wrapper will recognize individual WhatsApp JID/LID person-name shapes regardless of
caller-authored metadata and return a structured error. It will not substitute the top-level routing
entity because that is unsafe for groups. Normal named-entity creation, non-person entity creation,
and the separate deterministic unknown-sender reservation path remain unchanged.

### Decision 5: Reconcile only provably empty shells

The operator command will enumerate all distinct phone candidates rather than trusting first-match
bulk resolution. A candidate is plannable only when it has one live confirmed target and no roles,
user aliases, protected metadata, relationship facts, memory facts, text-object references, foreign
key references, protected account ownership, or conflicting review decision.

Dry-run output contains counts and a SHA-256 digest only. Apply requires `--apply` plus that digest,
recomputes the plan, and revalidates each pair under deterministic row locks. The audited relationship
merge transaction will be factored behind a FastAPI-free service, with an empty-shell precondition
executed in the same transaction. The command stops on the first failed postcondition. Pair
transactions remain independently audited commits; if a later stop follows one or more commits, the
operator receives a content-blind partial-apply count and stop category rather than a false rollback
claim.

Alternatives rejected:

- ad hoc SQL would bypass audit and reference handling;
- calling the current general memory merger can strand relationship references;
- broad cleanup of referenced sources would expand this feature into a merge-system redesign.

## Risks / Trade-offs

- [Risk] Resolving all group speakers creates more legitimate unknown entities. → Reuse the existing
  durable reservation and notification claims, and create at most one transitory entity per identity.
- [Risk] Identity lookup latency grows with group size. → Deduplicate identities per batch and use the
  existing batched canonical resolver for the known-sender pass.
- [Risk] The signal-extraction model may omit or alter identity fields. → Treat message ID as the join
  key and restore identity from authoritative input after model output normalization.
- [Risk] Unmapped LIDs cannot phone-match. → Preserve an opaque structured reservation key, use a
  neutral label, and keep the entity reviewable rather than guessing.
- [Risk] Reconciliation races with new ingress or user review. → Exact plan digest, pair locks,
  in-transaction revalidation, existing decision checks, and abort-on-drift.
- [Risk] Operator diagnostics leak contacts. → Aggregate categories and opaque fingerprints only;
  never log names, phone numbers, JIDs, raw evidence, or SQL arguments.

## Migration Plan

1. Ship spec, connector, identity, decomposition, memory guard, and reconciliation command together.
2. Deploy the forward identity repair before any reconciliation apply run.
3. Verify healthy connector heartbeat, zero ingestion errors, canonical known-sender resolution, and
   no newly created JID/LID fact-storage entities.
4. Run the reconciliation command in dry-run mode and review category counts and digest.
5. In a separate explicit invocation, supply `--apply` and the exact digest.
6. Run dry-run again; planned count must be zero while unmatched/referenced categories remain intact.

Rollback disables the new additive speaker enrichment and memory guard while preserving the original
transport channel and raw provider payload. Reconciliation tombstones are audited entity merges and
are not automatically reversed; this is why apply is separately authorized and limited to empty
shells.
