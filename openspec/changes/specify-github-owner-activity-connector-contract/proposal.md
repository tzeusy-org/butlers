# Define the GitHub owner-activity connector contract

## Why

The owner's defining professional activity — software development — is entirely
unperceived by the butler system today. `bu-dlelt` ("GitHub connector: PAT events poll
for occupation perception") is the retained implementation carrier for this outcome, but
its current `design`/`acceptance_criteria` have authority and correctness gaps a
`bu-27dxl.15` shaping pass found on 2026-09-06 (evidence:
`coordinator-evidence/run6-shaping-20260906/bu-27dxl.15-proposal.md`,
`coordinator-review.md`):

- it asserts the connector "routes events to the Chronicler" directly, bypassing the
  Switchboard/`ingest.v1` ingestion primitive every other connector uses;
- it names credential storage as "existing secrets infrastructure" and a "butler_secrets
  table" that does not match the actual RFC 0004 `public.entity_info` + companion-entity
  model;
- it asserts a PAT scope (`repo, read:org`) without a least-privilege decision, and does
  not distinguish fine-grained from classic PATs;
- it promises 5-minute freshness, which contradicts the official GitHub Events API's own
  disclosed latency;
- it omits RFC 0003/0004 registration, connector-base-contract obligations (cursor
  persistence, filtered-event flush, replay queue, heartbeat, metrics, rate limiting),
  and private-repository sensitivity/redaction entirely.

The binding coordinator correction for that shaping pass (`coordinator-review.md`) holds
that **`bu-dlelt` must retain its implementation outcome and must not be repurposed,
duplicated, or closed after prose alone** — a separate spec prerequisite repairs its
authority and design gaps instead. This change is that prerequisite. It defines the exact
contract `bu-dlelt`'s eventual implementation must satisfy; it does not touch `bu-dlelt`,
does not implement anything, and grants no runtime, provider, or credential authority.

## What Changes

- Add a new `connector-github` capability defining the GitHub owner-activity connector:
  a least-privilege, fine-grained-PAT-only, polling connector grounded in the official
  authenticated-user Events API
  (<https://docs.github.com/en/rest/activity/events>, confirmed live 2026-09-09).
- Specify exact endpoint (`GET /users/{username}/events`), pagination, ETag/304
  conditional-request, and `X-Poll-Interval`-driven cadence behavior — explicitly
  disclosing, not hiding, the provider's own **30-second-to-6-hour eventual-consistency
  latency** and its **300-event / 30-day rolling window** (no historical backfill beyond
  that window is possible through this endpoint).
- Specify the account/credential model: a `public.github_accounts` registry table plus a
  companion `public.entities` row (role `github_account`) anchoring a new
  `public.entity_info` type (`github_pat`, `secured=true`), mirroring the existing Steam
  connector's account pattern (`connector-steam`, `src/butlers/steam_account_registry.py`)
  rather than inventing a new credential-storage shape.
- Mandate **fine-grained PAT with the `Events` (read) permission only**; explicitly reject
  classic PAT (`repo`/`read:org`) as insufficiently least-privilege, since it grants far
  broader access than reading events requires.
- Define cursor/dedup semantics keyed on GitHub's monotonically increasing event `id`
  (not `created_at`, which the provider's own latency disclosure makes an unreliable
  ordering key), private-repository redaction (metadata-tier ingestion by default for
  private-repo events; no diffs, commit messages, PR/issue bodies), an explicit event-type
  allowlist, rate-limit/abuse-limit handling, and account isolation for independent
  accounts.
- Define deterministic downstream routing: the connector submits through the Switchboard's
  `ingest.v1` MCP path like every other connector (no direct Chronicler call), and a new
  global `ingestion_rules` skip row (mirroring the ActivityWatch and wellness precedents in
  RFC 0003 Amendments 1-2) routes `source_channel = "github"` events to a durable evidence
  table for future Chronicler occupation-category projection, without spawning an LLM
  classification session per event.
- Record, as drafted (not-yet-applied) amendment text in `design.md`, the exact RFC 0018
  (promotion rationale — GitHub activity is not currently listed as in-scope or deferred),
  RFC 0003 (new `github`/`github` channel/provider pair), and RFC 0004 (new `github_pat`
  registered `info_type`, new `github_account` role) changes a future implementation PR
  must apply. This change does **not** edit the RFC files themselves: `bu-dlelt` remains
  unimplemented, and amending an RFC to claim a pairing or credential type is "registered"
  before any code enforces it would misstate the system's actual behavior — the same
  reasoning `about/heart-and-soul/v1.md:210` gives against building ahead of an approved
  need.
- No amendment to `connector-base-spec` or `chronicler-source-compatibility`: the GitHub
  connector conforms to the existing shared connector contract and Chronicler-compatibility
  declaration mechanism without requiring a new generic requirement, and both files
  already carry other unarchived `## MODIFIED Requirements` blocks (see Impact) that make
  an additional one here an unnecessary overwrite hazard.

## Capabilities

### New Capabilities

- `connector-github`: least-privilege GitHub owner-activity connector and
  credential/account contract grounded in the official authenticated-user Events API.

### Modified Capabilities

None. This is a new-capability-only draft; no existing spec file is touched.

## Impact

- Affected future code (not touched by this draft): `src/butlers/connectors/github.py`
  (new), `public.github_accounts` (new table, migration TBD), `connectors.github_cursors`
  / `connectors.github_events` (new tables in the `connectors` schema per RFC 0006),
  `ENTITY_INFO_TYPES` (`github_pat` addition), `roster/switchboard/tools/routing/
  contracts.py` (`SourceChannel`/`SourceProvider` `github` addition, once implemented).
- Affected RFCs (drafted amendment text only, in `design.md`; not applied here): RFC 0018,
  RFC 0003, RFC 0004.
- `bu-dlelt` disposition: retained unmodified as the implementation carrier per the binding
  2026-09-06 coordinator correction. This draft is a prerequisite `bu-dlelt`'s
  implementation must satisfy, not a replacement for it; no Beads mutation is performed or
  requested by this change.
- Coexistence note: `connector-base-spec` currently carries unarchived
  `## MODIFIED Requirements` blocks from `repair-whatsapp-identity-reconciliation`,
  `define-infrastructure-reliability-lifecycle`, `connector-base-spec-live-catalog-cleanup`,
  `connector-runtime-instance-authority`, and `add-connector-oauth-scope-surface`.
  `chronicler-source-compatibility` carries blocks from
  `reconcile-chronicler-google-health-source-contract` and
  `capture-spotify-spoken-sessions`. This draft avoids adding to either file for exactly
  the reason `AGENTS.md`'s two-unarchived-changes-same-requirement hazard describes, and
  because no new generic requirement is actually needed.
- No implementation, provider/account/credential/data access, runtime/configuration,
  message delivery, activation, deployment, or merge is performed or authorized by this
  change. Expected tests: `+0 ~0 -0`.
