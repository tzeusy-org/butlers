# Owner route and contract reconciliation

Baseline: `5221178fbcfe60edeec0b7af31b71c7d55f0639e`. Source audit recursively
enumerated `create_app(api_key="").routes`, descending `original_router.routes`
without executing lifespan: **603 unique mounted method/path pairs** including
roster routers. This is source evidence, not a live runtime validation. Exact
local evidence is `decisions/passkey-preparation/route-inventory.json` in the
September 15 questionnaire packet. The list below never controls enforcement;
the central boundary protects every route except exact design D4 exemptions.

## Reconciliation completed in this candidate

The central `dashboard-owner-auth` admits configured header authority or a
server-managed owner session; passkeys issue that session. Cookie-backed unsafe
actions additionally require synchronizer CSRF and exact Origin. Independent
owner entity/contact, domain, CAS, body limits, idempotency and approval checks
remain after central authentication. Dedicated auth-store reads are necessarily
permitted before domain reads. Missing key is not unavailable authority when a
healthy keyless session exists. No old mechanism-neutral E1/E2 preference remains.

Canonical changes are carried as whole MODIFIED blocks under this owning change
(`dashboard-admin-gateway`, `dashboard-relationship`, `butler-health`). Active
sources below have their auth clauses reconciled in place, retaining scenario
names and existing IDs, adding missing metadata with unused capability IDs.
These active-file amendments are part of this exact successor adoption/review;
prior independent artifacts do not certify the edited bytes. Archive remains
serialized: refresh same-name overlaps and rebuild against the baseline after
each predecessor archives. No overwrite baseline was weakened.

| Source | Whole requirement headings to reconcile |
| --- | --- |
| openspec/specs/dashboard-admin-gateway/spec.md | Defense-in-Depth API-Key Authentication (Opt-In, Not Fail-Closed); Honest Auth-Status Health Indicator (must distinguish API key enabled from overall owner auth enabled) |
| openspec/specs/dashboard-relationship/spec.md | Owner-only authorization for entity endpoints |
| openspec/specs/butler-health/spec.md | [TARGET-STATE] Health Voice briefing route |
| openspec/changes/harden-runtime-auth-and-breaker-attention/specs/dashboard-model-settings/spec.md | Catalog Test Uses a Runtime Probe, Not a Dashboard-Local Adapter; Model Breaker Attention Episode Visibility and Reissue; Catalog Verify-All API (ensure inherited owner enforcement explicit and coherent) |
| openspec/changes/harden-runtime-auth-and-breaker-attention/specs/dashboard-spend-dashboard/spec.md | Fleet-Halt Visibility |
| openspec/changes/harden-runtime-auth-and-breaker-attention/specs/runtime-attention-outbox/spec.md | Explicit Uncertain-Episode Reissue |
| openspec/changes/specify-home-presence-owner-entity-configuration/specs/home-presence-configuration/spec.md | Owner-authenticated dashboard-only presence entity settings surface; This contract does not claim to close the pre-existing generic disclosure and write paths (global authentication now protects generic dashboard routes, while validation/CAS bypass still remains) |
| openspec/changes/specify-roster-identity-owner-operations-overlay/specs/dashboard-butler-management/spec.md | System Prompt Versioning API |
| openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md | Exact Message Ingress Recovery API |
| openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-terminal-action-recovery/spec.md | Action State, Turn Mapping, and Manual Resolution |
| openspec/changes/generation-fenced-codex-auth-rotation-provenance/specs/dashboard-api/spec.md | Dashboard Codex Mutations Use Shared Generation Precedence; Dashboard Device Authentication Has a Durable Prelaunch Fence |
| openspec/changes/memory-honesty-last-mile/specs/dashboard-api/spec.md | Owner-Scoped Dead-Letter Episode Requeue API |


Additional disposition: Home `Implementation and use remain separately gated`
now names the exact successor adoption and implemented-session prerequisite;
it does not reopen the already-recorded configured-key direction. Existing
owner-review and private-data operational gates remain. Domain Home generic
state/MCP bypass remains for validation/CAS, while generic dashboard HTTP is
centrally authenticated. Health fallback/never-raise only applies after admission,
so an auth failure cannot produce a private templated briefing or hit its cache.
Auth posture retains the API-key boolean but never describes keyless protection
as disabled authentication.

PR4050's adopted `specify-ha-person-entity-mapping` artifact is off-branch and
**unchanged**. Its explicitly permitted equivalent owner-approved successor
composes with this boundary: key/session authentication precedes its 32 KiB raw
body limit, mapping-domain checks and CAS. That artifact's exact adoption is
preserved; no private mapping values, provider calls or rollout are authorized.

## Runtime and frontend seams

Existing header-only `ApiKeyMiddleware` and separate header-only
`require_dashboard_owner_control` must converge on the one verified request
context. Models Test/Verify enforcement and working browser access ship together.
`authenticated_principal()` is attribution, not proof. CORS/OPTIONS cannot expose
protected data. Only exact GET `/health` and `/api/health` are probes; the mounted
GET `/api/health/briefing` and all other Health methods remain private.

The shell and app-level timezone queries must not mount before authentication.
`apiFetch` changes must also cover raw fetch paths for ICS import and conversation
streams, plus EventSource/stream consumers. Logout/401 closes streams, cancels
queries and clears protected caches; unsafe actions are not replayed after login.
API redirects preserve existing API_BASE_URL path-prefix resolution.

Tailscale Serve maps frontend and API under separate paths of one origin.
Origin/RP do not include paths. Same-host dev/prod share the WebAuthn RP/origin;
path prefixes are not security isolation. Deployment must avoid auth-cookie
collisions and prove instance/challenge/credential binding, including rejection
of another instance's credentials. Distinct configured hostnames are required
for actual origin isolation, never inferred from paths. Although compose supports
a configurable Tailscale HTTPS port, this successor deliberately supports the
canonical port 443 only; alternate-port auth configuration is unavailable rather
than inferred or silently accepted. No proxy or hostname
change was executed for this preparation.

## Historical source inventory from PR4164

The following is preserved original inventory evidence, not current exemption
wording. References to D5/D7 below refer to the original mechanism-neutral PR;
current ceremony and output rules are design D4/D8. Current rules accept valid
configured key authority or owner session and override the historical 'session'
shorthand. The three historical exception classes are replaced by exact D4
method/path exemptions. Current recursive route count is 603 above.

### Owner-gated browser route inventory

The global rule is broader than this list: after keyless cutover every
dashboard `/api/*` route requires a session except the three narrow exception
classes in D5. The inventory below is the complete additional owner-only set
named by current source, canonical specs, and unarchived deltas at the source
baseline. A future route tagged or specified as owner-only joins the set
automatically and must not depend on this hand-maintained list for enforcement.

| Contract/source | Method and route | Additional check |
| --- | --- | --- |
| Implemented `require_dashboard_owner_control` | `GET /api/settings/models/attention` | runtime-attention state |
| Implemented `require_dashboard_owner_control` | `POST /api/settings/models/attention/{episode_id}/reissue` | uncertain-state/idempotency |
| Implemented `require_dashboard_owner_control` | `GET /api/spend/runtime-attention` | fleet-halt state |
| `harden-runtime-auth-and-breaker-attention` | `POST /api/settings/models/{entry_id}/test` | catalog/probe control |
| `harden-runtime-auth-and-breaker-attention` | `POST /api/settings/models/verify-all` | rate/concurrency/probe control |
| `specify-home-presence-owner-entity-configuration` | `GET`, `PUT /api/home/settings/presence/owner-entities` | Home validation/CAS |
| `specify-roster-identity-owner-operations-overlay` | `GET`, `PUT /api/butlers/{name}/prompt` | roster/pool/overlay CAS |
| `specify-roster-identity-owner-operations-overlay` | `GET /api/butlers/{name}/prompt/history` | roster/pool/history |
| `specify-roster-identity-owner-operations-overlay` | `PUT /api/butlers/{name}/prompt/mode` | rollback-window CAS |
| `durable-dashboard-terminal-action-recovery` | `POST /api/butlers/{name}/conversation-turns/{message_id}/retry-ingress` | durable ingress fence |
| `durable-dashboard-terminal-action-recovery` | `GET /api/dashboard/terminal-actions/{id}` | action ownership/read model |
| `durable-dashboard-terminal-action-recovery` | `POST /api/dashboard/terminal-actions/{id}/resolve` | immutable resolution |
| `memory-honesty-last-mile` | `POST /api/memory/episodes/{episode_id}/requeue` | recovery eligibility/idempotency |
| canonical `dashboard-briefing` | `GET /api/dashboard/briefing` | owner-contact assertion/cache |
| canonical `butler-health` | `GET /api/health/briefing` | Health owner assertion/per-owner cache |
| canonical `system-overview-page` | `GET /api/system/egress` | owner-contact assertion |

Canonical `dashboard-relationship` Clause 12 adds this exact set. The central
session boundary runs before its owner-role assertion:

- `POST /api/relationship/entities`
- `POST /api/relationship/entities/{id}/merge`
- `POST /api/relationship/entities/{id}/archive`
- `POST /api/relationship/entities/{id}/promote-tier`
- `DELETE /api/relationship/entities/{id}`
- `POST /api/relationship/entities/queue/dismiss`
- `POST /api/relationship/entities/{id}/contacts`
- `DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}`
- `POST /api/relationship/entities/{id}/notes`
- `POST /api/relationship/entities/{id}/interactions`
- `POST /api/relationship/entities/{id}/gifts`
- `POST /api/relationship/entities/{id}/reach-out-drafts`
- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`
- `GET /api/relationship/plex/halo`

The canonical Secrets surface is owner-operated in v1 and the active
`generation-fenced-codex-auth-rotation-provenance` delta explicitly treats
Codex save/rotate, reauthorization/device-auth, probe, and revoke as owner
operations. Therefore the additional inventory also includes:

- `PUT`, `DELETE /api/butlers/{name}/secrets/{key}`
- `PUT`, `DELETE /api/oauth/google/credentials`
- `POST /api/secrets/user/{provider}/reauthorize`
- `POST /api/secrets/user/{provider}/rotate`
- `POST /api/secrets/user/{provider}/disconnect`
- `POST /api/secrets/user/{provider}/probe`
- `POST /api/secrets/system/{key}`
- `POST /api/secrets/system/{key}/probe`
- `DELETE /api/secrets/system/{key}`
- `POST /api/secrets/cli/{credential_id:path}/rotate`
- `POST /api/secrets/cli/{credential_id:path}/revoke`
- `POST /api/secrets/cli/{credential_id:path}/reauthorize`
- `POST /api/secrets/probe-all`

The CLI rotate path is spelled with `{credential_id:path}` as mounted, not the
canonical spec's shorthand `{id}`, because credential identifiers contain a
slash. It retains its separately sanctioned one-time credential response; D7
distinguishes that response from owner-auth material.
