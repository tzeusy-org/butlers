# Connector descriptor consolidation design

## Governing source

The released bu-dss7he.9 S1 design and its three folded moves require one static
identity home without weakening transport or credential boundaries. The current
connector-base-spec remains authoritative; bu-lsxqb0.19's future registry split is
not adopted. This delta changes only S1 discovery/static-data behavior. It does
not remove the current environment or health-server requirements before the
later slices actually replace those mechanisms.

## S1 model and projections

`butlers.connectors.catalog` contains frozen data with no environment, application,
DB or credential reads. Its thirteen canonical identities match the existing
heartbeat registration set. The three additional preview labels (`telegram`,
`telegram_user`, `live-listener`) remain explicit display aliases. They confer no
registration or ingress authority. Live Listener keeps its distinct hyphenated
stored-event spelling. Discovery projects deployable records; Helm support is a
subset and does not claim that any deployment is enabled or reachable.

Connector primary and secondary pairs plus explicit nonconnector ingress retain
the exact nineteen-pair accepted relation. API/MCP/dashboard, IMAP, Slack and Home
Assistant's secondary wellness emission are not deleted to force raw set equality.
Retired Discord remains refused for registration and ingestion. Stored-data
history readers are a separate later-slice obligation.

The discovery endpoint constructs the existing four-field profiles directly from
these records. Its existing order is retained, with the already-supported local
Live Listener appended. The scrubber changes only its closed fallback-label
source; detector, label validation, fail-withhold and idempotency behavior stay.
Existing later-slice literal populations are fenced by exact owner and labels,
rather than exempting entire files from the anti-duplication check.

## Remaining original slices

S2 derives wire-pair/default/OAuth scope and auth-UI/history projections, preserving
all existing accepted pairs, explicit unsupported selectors, observed-scope
redaction, TOML precedence, consent sets and connector-owned recovery. S3 makes
connector identity static, removes identity env reads/Compose leaves and consolidates
Gmail environment loading without deleting real per-account config or credential
provenance. Spotify's unused identity field and all folded source/doc obligations
remain in scope.

S4 requires an actual shared owned health-server lifecycle first. It derives ports,
Helm fragments, service addresses and log paths, retaining OwnTracks webhook auth,
Gmail's main-loop reload signaling, separate webhook/per-account port0 semantics,
Google deployment separation, and caller-specific unavailable behavior. Adding a
Service while keeping a loopback listener is insufficient. No health-kit,
service/bind, Helm, OAuth reload or Steam health change is included in S1.

## Assurance and rollback

Use the actual existing HTTP consumer mismatch as RED. Preserve all old membership
cases and four-field/no-DB checks; exercise every old safe label plus unknown,
code/link injection and detector failure. Check real accepted/rejected pair models
and actual emitter defaults independently of the new table. A contribution inverse
must break the consumer and exact restoration must pass. Record per-slice measured
LOC/file delta and removed-mechanism survivors. No count or static projection is
live deployment, native provider or full-feature acceptance evidence.
