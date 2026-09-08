## Why

`about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md` has, until this
change, deferred "Readwise / Pocket (reading highlights)" as a single P2 row. Pocket is no longer
a candidate at all: Mozilla shut the service down on 2025-07-08 and ended API transactions on
2025-10-08. Readwise remains technically viable but nothing in the repo grounds it — no connector,
no credential type, no cursor/rate-limit/deletion contract exists, and the shipped
`ReadingInferredAdapter` (`src/butlers/chronicler/adapters/reading.py`) explicitly documents
Readwise/Pocket as a "future extension path (not in v1)".

`bu-27dxl.15` (perception expansion: GitHub connector, Spotify podcast lens, YouTube scope-set,
reading capture) names reading capture as a remaining outcome. The coordinator-reviewed run-6
shaping packet
(`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/coordinator-review.md`,
`bu-27dxl.15-proposal.md`) requires a Readwise-only spec-first prerequisite — Candidate D — before
any implementation carrier can exist, and explicitly forbids inferring implementation from this
draft.

This change retires Pocket with official evidence, drafts the full Readwise connector contract
grounded in Readwise's official public API documentation (https://readwise.io/api_deets, fetched
2026-09-09), and amends RFC 0018's deferral entry accordingly (see the RFC 0018 amendment applied
alongside this change). It performs no account, token, credential, provider, or runtime action.

## What Changes

- Retire Pocket from RFC 0018's deferred-connector catalogue with official shutdown evidence, and
  promote Readwise from a blanket P2 deferral to spec-first status pointing at this change (RFC
  0018 Amendment 1 — applied directly to the RFC file, since it is a scope/deferral record, not an
  OpenSpec capability baseline).
- Define a new `connector-readwise` capability: a single-account (owner-only), token-authenticated
  polling connector implementing the full `connector-base-spec` contract — identity/auth, the
  Readwise `/v2/export/` cursor/pagination model, rate-limit/backoff, dedup/update event identity,
  a bounded deletion-reconciliation poll, filtered-event buffering, replay-queue draining,
  heartbeat, and Prometheus metrics.
- Specify content handling that keeps captured highlight/note text out of LLM classification by
  default: a metadata-tier `ingest.v1` envelope (`payload.raw = null`) plus a pre-resolved global
  `metadata_only` policy rule, mirroring the existing Spotify spoken-session
  (`connectors.spotify_spoken_sessions`) and Steam status-change precedents. Full highlight
  content lives only in a new connector-owned, least-privilege table,
  `connectors.readwise_highlights`.
- Specify truthful annotation provenance: a captured highlight/book update is evidence of a saved
  annotation only. No reading-duration or completion state is derived from it. This is a distinct,
  additive source from the existing `ReadingInferredAdapter` calendar/health-fact signal, which is
  unmodified by this change.
- Propose (but do not apply) an RFC 0003 `reading`/`readwise` canonical channel/provider pairing
  and an RFC 0004 `entity_info` credential type `readwise_token`. Per this repo's existing
  amendment convention (RFC 0003 Amendments 1-2, RFC 0004 Amendment 2 — each applied together with
  the implementation that makes the enum values real), those two RFCs are amended at
  implementation time, not by this drafting-only packet.
- Name the actual owner subscription/token/content-consent decision as the execution gate this
  draft does not and cannot satisfy, and flag the handful of product/privacy choices reserved for
  owner review in `design.md`.

No code, migration, credential, dashboard, runtime, or provider-call change ships in this packet.

## Capabilities

### New Capabilities

- `connector-readwise`: contract for a single-account, token-authenticated, capture-only Readwise
  highlight/book connector.

### Modified Capabilities

None inside `openspec/specs/`. RFC 0018 (a doctrine RFC, not an OpenSpec capability) is amended
directly — see `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md`
Amendment 1.

## Impact

- **Code**: none. This is a documentation/specification-only change.
- **Database**: none. `connectors.readwise_highlights` and the `readwise` cursor row are specified
  for a future implementation carrier, not created here.
- **RFCs**: `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md` is amended
  (Pocket retired, Readwise promoted to spec-first). RFC 0003 and RFC 0004 are **not** amended by
  this change; their proposed amendments are recorded in `design.md` for implementation time.
- **Execution gate**: actual Readwise account/subscription/token provisioning and consent to read
  the owner's private highlight content require a separate, explicit owner act. This proposal
  authorizes drafting only.

Tests: +0 ~0 -0.
