# Connector descriptor consolidation design

## Governing source

The released bu-dss7he.9 S1 design and its three folded moves require one static
identity home without weakening transport or credential boundaries. The current
connector-base-spec remains authoritative; bu-lsxqb0.19's future registry split is
not adopted. This delta implements S1 discovery/static-data behavior, S2 consumer projections
and S3 static runtime identity and Gmail parser consolidation. Operational
environment and health-server requirements remain where their mechanisms survive.

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

## S2 adapters and explicit selectors

The same frozen rows own policy selectors, stored-channel history, public auth
classification, ordered/versioned scope manifests and closed recovery capabilities.
The API keeps its existing `ScopeDecl`, `ScopeManifest` and `ConnectorAuthEntry`
interfaces and status/redaction algorithms. Mutable API lists/dicts are fresh
projections of immutable tuples. Wire `Literal` vocabularies remain independent
protocol fences; the accepted relation derives exactly nineteen pairs.

Connector, provider and source-channel resolution are explicit namespaces. A
Telegram provider resolves both bot and user-client members; email retains IMAP
as an unsupported alternative, and wellness retains both Home Assistant and
Google Health. Neither a shared group nor an auth-only alias grants registration
or chooses an enrollment flow. The thirteen default-policy selectors and twelve
stored-history keys remain exact; Discord is a stored-only reader, never an active
connector. Canonical WhatsApp and its legacy alias use managed whatsmeow session
classification and the existing consent-driven Passport pairing path. ActivityWatch
is local environment configuration, with no remote enrollment. Its existing
`static-token` fallback wire category is not a claim of a bearer-token flow.
Canonical/legacy Live Listener and unsupported selectors use the actual connector
roster route for observation only. No observed expiry or validity is fabricated.

Google's six scope groups and default tuple retain their exact ordering, TOML
precedence and explicit Health opt-in. All five manifest versions and metadata
are unchanged. The pre-existing Spotify mismatch remains disclosed: registry
minimum scopes include identity scopes absent from the connector-owned ten-scope
PKCE request. This relocation does not change consent or silently reconcile them.

`render_connector_catalog.py` renders only selector-to-recovery-kind and Health
opt-in data. `--check` refuses stale output. The actual TypeScript consumer uses
an own-property lookup, literal Google provider and existing Passport routes;
unknown/prototype names and retired Discord remain unsupported. The separate
API DTOs and peer response-model migrations are outside this slice.

## S3 runtime identity and shared parsing

Strict canonical lookup binds every connector emitter and configuration loader to
its descriptor, refusing aliases, retired or unknown rows. Home Assistant keeps
both primary and wellness pairs; Live Listener keeps distinct runtime and stored
spellings. Twenty identity environment reads and twenty-six Compose leaves are
removed. Nonidentity inputs and all event/endpoint/cursor keys remain unchanged.
Only Spotify's two proven unread identity fields disappear.

Gmail shares small parser stages rather than introducing a catchall configuration
mode. Fifteen common settings retain original read/validation order and each
entrypoint's distinct boolean rules. Four legacy-runtime-only settings and the
process-only account rescan interval remain separate. Per-account metadata,
credential origin, explicit webhook-token overrides and account health port zero
remain unchanged. Complete original environment requirements and scenarios are
carried forward with only static identity authority replaced.

## Remaining original slice

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
