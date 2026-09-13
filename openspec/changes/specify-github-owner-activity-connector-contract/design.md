## Context

`bu-dlelt` is the retained implementation carrier for GitHub owner-activity perception
(occupation category is currently unfed by any real evidence of the owner's software
development work). Its current `design` field describes a connector that calls
`https://api.github.com/users/{username}/events`, stores the PAT in an unspecified
"existing secrets infrastructure," and routes normalized events "as domain events for
Chronicler occupation-category ingestion" — i.e. a direct connector-to-Chronicler call,
bypassing the Switchboard entirely. Every implemented connector in this repository
(`openspec/specs/connector-*`) instead submits `ingest.v1` envelopes to the Switchboard via
MCP; nothing in the codebase gives a connector Chronicler write access directly. `bu-dlelt`
also asserts PAT scopes (`repo`, `read:org`) without a least-privilege analysis, and a
5-minute polling cadence that is incompatible with the provider's own documented latency.

The 2026-09-06 `bu-27dxl.15` shaping pass (evidence:
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/`) found these
gaps and proposed reshaping `bu-dlelt` itself into a spec-only packet. The binding
coordinator review overruled that: `bu-dlelt` **retains its implementation outcome** and is
**not repurposed or closed after prose alone**. This change is the separate spec
prerequisite the review calls for. It is read against `bu-dlelt`'s current content only to
identify the gaps a future implementation must close — it changes nothing about `bu-dlelt`
itself.

Live-fetched official documentation (<https://docs.github.com/en/rest/activity/events>,
2026-09-09) grounds every provider-facing claim below:

- `GET /users/{username}/events` returns the authenticated user's own events, including
  private ones, when the caller authenticates as that user.
- "This API is not built to serve real-time use cases. Depending on the time of day, event
  latency can be anywhere from 30s to 6h."
- The timeline holds at most 300 events and only events from the past 30 days.
- `X-Poll-Interval` states the caller's allowed polling frequency and must be honored;
  under high server load the interval may increase.
- ETag / `If-None-Match` conditional requests return `304` without consuming the caller's
  request-based rate-limit budget as a full poll.
- A fine-grained PAT can be scoped to the `Events` user permission (read) alone, sufficient
  to read private events for the authenticated user.
- `page` / `per_page` (max 100) paginate; default `per_page` for this endpoint is 30.

The nearest implemented precedents are `connector-steam` (PAT-style credential, per-account
polling, state-diff-based event detection, multi-account registry with a companion entity)
and RFC 0003 Amendment 2 (`activitywatch`/`activitywatch`: a high-frequency evidence
channel that lands in a durable evidence table for direct Chronicler-adapter consumption,
bypassing LLM classification via a global `ingestion_rules` skip row, exactly the "not a
direct Chronicler shortcut" pattern this connector needs).

## Goals and non-goals

Goals:

- Define one concrete, official-docs-grounded contract for polling the owner's own GitHub
  activity, with an explicit least-privilege credential/account model.
- Make the provider's actual eventual-consistency behavior (30s-6h latency, 300-event/
  30-day window) a first-class, disclosed property of the contract — in cursor semantics,
  health/status reporting, and explicit non-promises — rather than an implementation
  afterthought.
- Route every event through the existing Switchboard `ingest.v1` primitive, with
  deterministic (not LLM-classified) downstream handling, so this connector needs no new
  generic base-contract or Chronicler-compatibility mechanism.
- Specify private-repository sensitivity handling precisely enough that a future
  implementation cannot accidentally ingest full commit messages, diffs, or PR/issue
  bodies from private repositories as `normalized_text`.
- Name the exact RFC 0018/0003/0004 amendment text a future implementation PR must apply,
  without applying it now.

Non-goals:

- No implementation, runtime process, database migration, credential provisioning, or any
  live GitHub API call. Nothing here executes.
- No webhooks, GitHub App, or OAuth App — PAT-based polling only, matching `bu-dlelt`'s
  original scope decision.
- No write access of any kind (no comments, no repo/Actions mutation).
- No GitHub Actions/workflow-run visibility, no repository-level analytics (stars, traffic,
  dependency graphs) — this is owner-activity perception, not repo observability.
- No hard real-time SLA. The provider does not offer one; this contract does not invent
  one.
- No Chronicler adapter implementation or `chronicler_compatibility` declaration filing —
  that is `chronicler-source-compatibility`'s own separate, future spec-delta once this
  contract is accepted and `bu-dlelt` is scheduled (see D9).
- No modification to `bu-dlelt`'s Beads fields. No Beads mutation of any kind — this
  worker has no lifecycle-mutation authority, and the binding coordinator correction
  reserves `bu-dlelt`'s reshaping (if any) for the coordinator, not this draft.

## Decisions

### D1: Fine-grained PAT only; classic PAT is explicitly rejected

A fine-grained PAT scoped to exactly the `Events` user permission (read) is the sole
supported credential type. Classic PATs are rejected: reading another user's — or your
own — private events with a classic token requires the broad `repo` scope (full read/write
on all accessible repositories) or `read:org`, both far exceeding what this connector
needs. This is a direct application of the bead's own least-privilege charge, not a
product/privacy decision reserved for owner review — it is the only technically
justifiable choice given the two token types' actual permission shapes.

### D2: Account model mirrors Steam's registry pattern

`public.github_accounts` (id UUID PK, `entity_id` UUID FK to a companion
`public.entities` row with `roles = ['github_account']`, `github_user_id` BIGINT,
`github_login` TEXT, `is_primary` BOOLEAN, `status` TEXT — `active` / `revoked` /
`suspended` — `metadata` JSONB, timestamps) is the account registry, exactly mirroring
`public.steam_accounts` / `steam_account_registry.py`. The PAT itself lives in
`public.entity_info` on the companion entity: `info_type = 'github_pat'`,
`secured = true`. This is a deliberate reuse of an existing, working pattern rather than a
new credential-storage shape — the gap `bu-dlelt`'s current design leaves by naming a
non-existent "butler_secrets table."

**Account cardinality:** the schema supports N independently polled accounts (like Steam),
with a partial-unique "at most one primary" constraint. v1 expectation is a single account
(the owner's own GitHub login), but the multi-account shape costs nothing extra to specify
now and accommodates a real, plausible case (a developer with separate personal and work
GitHub identities) without a later schema migration. This is an engineering-allocation
choice, not a privacy/product one, so it is decided here rather than deferred.

### D3: Endpoint, pagination, and conditional-request behavior

The sole polling endpoint is `GET /users/{username}/events` (authenticated as that user, so
private events are included). `GET /users/{username}/events/public`, `/received_events`,
`/orgs/{org}/events`, `/repos/{owner}/{repo}/events`, and the global `/events` firehose are
explicitly out of scope: `received_events` describes activity from repos/users the account
follows or watches, not the owner's own authored activity, and the rest are broader or
public-only views this connector does not need.

- Steady-state poll: `GET` page 1 with `If-None-Match: "<last-etag>"`. A `304` means no new
  events; advance nothing, record a successful poll.
- On `200`, walk pages forward (`per_page=100`) collecting events until either the response
  is exhausted or an event with `id <= last_processed_id` (the persisted cursor) is
  encountered — GitHub's event `id` values are monotonically increasing across the whole
  event stream, so `id` is a safe stopping condition even though the provider's own latency
  disclosure makes wall-clock `created_at` an unsafe one (see D5).
- Respect `X-Poll-Interval` as the floor for the next poll's schedule; never poll more
  frequently than it states. Absent explicit guidance, default to 60 seconds, doubling
  under sustained `X-Poll-Interval` increases per GitHub's "in times of high server load"
  note.

### D4: First-poll baseline and the 300-event / 30-day provider window

On an account's first-ever poll (no persisted cursor), the connector establishes a
baseline: it records the newest event's `id` as the cursor **without** emitting `ingest.v1`
events for the existing timeline. This mirrors the Steam connector's owned-games baseline
behavior (no flood of synthetic "purchased" events for a pre-existing library) and is
necessary here for an additional, GitHub-specific reason: the endpoint exposes at most 300
events from the last 30 days, so there is no way to backfill genuine history through this
API at all. A future implementation MUST NOT claim to reconstruct occupation evidence
older than the account's connection time from this endpoint. If deeper history is ever
wanted, that is a distinct, separate connector/API decision (e.g. GraphQL contribution
calendar), explicitly out of scope here.

### D5: Cursor and dedup keyed on event `id`, not `created_at`; late/out-of-order handling

The persisted cursor per account is the last-processed event `id` (a numeric string,
compared as an integer) plus its `created_at` for observability only. The `ingest.v1`
`event.external_event_id` is `"github:<account_github_user_id>:<event.id>"`; deduplication
is by this key, matching the Switchboard's priority-2 dedup strategy (RFC 0003 /
`connector-base-spec`, "External event ID"). `event.observed_at` is the connector's own
poll timestamp (when it first saw the event), distinct from the GitHub-provided
`created_at`, consistent with `IngestEventV1`'s existing observed-vs-provider-timestamp
separation.

Because the provider itself discloses 30s-6h latency, an event can appear in a later poll
with a `created_at` earlier than events already processed (a genuinely late arrival, not a
bug). The connector MUST NOT assume events arrive in `created_at` order and MUST NOT drop
or reject a late-arriving event solely because its `created_at` precedes the cursor's
`created_at` — the `id`-based cursor already prevents reprocessing the same event exactly
once, and a late arrival is simply a new `id` greater than the persisted cursor, processed
normally with its true (older) `created_at` preserved.

### D6: Private-repository sensitivity and redaction

Every GitHub event carries a `repo` reference; the connector MUST resolve (from the event
payload) whether that repository is private. For a **private-repository** event:

- `control.ingestion_tier = "metadata"` (Tier 2): `payload.raw = null`.
- `payload.normalized_text` is a fixed-shape summary containing only: event type, the
  repository's full name (`owner/repo`), and a bounded count (e.g. "Pushed 3 commits to
  acme-corp/internal-service" or "Opened a pull request in acme-corp/internal-service").
  It MUST NOT include commit messages, PR/issue titles or bodies, review comment bodies,
  branch names beyond what the event type itself implies, diffs, or file paths.

For a **public-repository** event, `control.ingestion_tier = "full"` is permitted:
`payload.raw` carries the complete provider payload and `payload.normalized_text` MAY
include richer detail (e.g. an actual PR title), since the content is already public. This
mirrors the tiered-ingestion mechanism `connector-base-spec` already defines for other
connectors (Tier 1 "full" vs. Tier 2 "metadata") rather than inventing a new one.

No PAT value, fine-grained-PAT fingerprint beyond what the shared Secrets Passport
convention already exposes (see D8), or raw repository content appears in logs, metrics,
traces, or `connectors.filtered_events.full_payload` for a filtered/errored private-repo
event beyond the same redaction rule applied to `payload.raw`/`normalized_text` above.

### D7: Rate limiting — primary quota and secondary abuse detection

The connector inherits `connector-base-spec`'s existing "Rate Limiting and Backpressure"
requirement (honor `Retry-After`, exponential backoff with jitter, respect quotas) without
modification. GitHub-specific interpretation:

- **Primary quota** (`X-RateLimit-Remaining` / `X-RateLimit-Reset`, 5000/hour for an
  authenticated fine-grained PAT): the connector tracks remaining budget and, below 10%
  remaining, extends its own poll interval (never below the `X-Poll-Interval` floor) rather
  than stopping outright — a single account's poll cadence is a small fraction of the
  5000/hour budget under normal operation, so this is a soft degradation, not an expected
  steady state.
- **Secondary (abuse-detection) limit**: GitHub returns `403` with a `Retry-After` header
  when request *rate* (not quota) is judged abusive. The connector treats this exactly per
  `connector-base-spec`'s existing contract — honor `Retry-After` — and additionally
  reports `degraded` health while backing off, since a healthy poller should never trigger
  this signal at the account's configured cadence.

### D8: Secrets Passport inventory and probe behavior are content-blind

`github_pat` is a **generic User credential type** (like `steam_api_key`), not a
connector-owned OAuth exception (unlike Spotify's connector-managed Tier 2 tokens). It is
added to `ENTITY_INFO_TYPES` and participates in the existing generic
`/api/secrets/inventory`, `/api/secrets/user/{provider}`, `.../rotate`, `.../disconnect`,
and `.../probe` surfaces exactly as `steam_api_key` does today. Probe behavior: a probe
calls a minimal, read-only, low-cost GitHub endpoint (the token's own rate-limit status,
`GET /rate_limit`, which requires no scope and confirms the token authenticates) and
reports only success/failure plus the fixed `capability_categories = ["occupation-
activity"]` evidence — never the token value, the resolved `github_login`, or the raw
provider response body. This follows the same evidence-over-value contract
`butler-secrets` already defines for every other credential family; no new inventory or
probe mechanism is introduced.

### D9: Deterministic downstream routing — Switchboard `ingest.v1`, not a Chronicler shortcut

The connector submits every event as an `ingest.v1` envelope via the standard MCP `ingest`
tool, exactly like every other connector — `bu-dlelt`'s current design's direct
connector-to-Chronicler call is rejected outright; no such write path exists or is
proposed. `source.channel = "github"`, `source.provider = "github"`,
`source.endpoint_identity = "github:user:<github_login>"`.

Because these are high-frequency, non-conversational evidence events (like ActivityWatch),
a new global `ingestion_rules` row (`scope='global'`, matching `source_channel='github'`,
`action='skip'`) prevents LLM classification and butler-session spawning for every GitHub
event, mirroring RFC 0003 Amendment 2's `activitywatch` skip rule exactly. The connector
also persists each accepted event to a new durable evidence table,
`connectors.github_events` (mirroring `connectors.activitywatch_events`), which is the
future read surface for a Chronicler `github.activity` projection adapter. **Filing that
adapter's `chronicler_compatibility` declaration (per `chronicler-source-compatibility`)
is explicitly out of scope for this change** — it is `bu-dlelt`'s (or a successor's) own
future spec delta once this connector contract is accepted, kept separate so this draft
adds no requirement to a file two other unarchived changes already modify (see proposal.md
Impact).

### D10: Account isolation

Each account's polling loop, cursor (`connectors.github_cursors`, keyed by
`endpoint_identity`), rate-limit tracking, and health state are fully independent, exactly
per the Steam precedent: one account's revocation, rate-limiting, or transient failure MUST
NOT affect another account's polling loop or cursor.

### D11: Honest empty/unavailable state and provider-latency disclosure

- **No account connected:** the connector starts in idle/degraded mode (no active loops),
  matching `connector-steam`'s "No qualifying accounts" scenario.
- **Credential absent/invalid/revoked:** detected via a `401`/`403` on poll; the account
  transitions to `error` health, is not retried on the base poll schedule (avoiding
  wasted-quota hammering of a dead credential), and is only retried after the credential is
  re-verified via probe (D8) or reconnect.
- **Transient failure** (network error, `5xx`): exponential backoff and retry per
  `connector-base-spec`; does not transition to `error` until repeated failures exceed the
  base contract's threshold.
- **Provider-latency disclosure:** the connector's heartbeat/status payload includes a
  fixed, non-configurable disclosure field (e.g.
  `data_latency_disclosure: "GitHub Events API: not real-time; 30s-6h observed latency"`)
  so no dashboard, log, or operator-facing surface can present this connector's data as
  fresher than the provider itself guarantees. This is a documentation/status honesty
  requirement, not a runtime behavior change — it costs nothing to specify now and directly
  answers the shaping pass's "do not promise five-minute freshness" finding.

## Drafted RFC amendment text (not applied by this change)

The following is the exact text a future implementation PR should apply to each RFC's
"Amendments Applied" section (RFC 0003, RFC 0004) or body (RFC 0018), once `bu-dlelt` is
actually implemented and the corresponding code exists. It is recorded here, in full, so
the implementer does not have to re-derive it — but it is **not applied to the RFC files by
this change**, because doing so before any code registers the `github`/`github` pairing or
the `github_pat` type would assert something false about the system's current behavior
(see proposal.md "What Changes").

**RFC 0018 (new roster entry, not currently listed as in-scope or deferred):**

> GitHub owner-activity polling is added to the v1 connector roster once `bu-dlelt` ships:
> a PAT-based (fine-grained, `Events:read` only) poller of the authenticated user's own
> GitHub Events API, feeding occupation-category evidence. It is not a general-purpose
> GitHub integration (see `connector-github`'s explicit non-goals: no webhooks, no GitHub
> App/OAuth App, no writes, no Actions visibility, no repository analytics).

**RFC 0003 Amendment N — GitHub Owner-Activity Channel:**

> Applied per `bu-dlelt`'s implementation. A new `github`/`github` channel/provider pair is
> registered for the GitHub owner-activity connector (`src/butlers/connectors/github.py`).
> Like ActivityWatch (Amendment 2) and Home Assistant (Amendment 1), GitHub events are
> high-frequency evidence whose value lives in the durable evidence table
> (`connectors.github_events`) consumed directly by a future Chronicler `github.activity`
> projection adapter — not in LLM-classified natural language. A global
> `source_channel='github'` skip rule bypasses LLM classification for these events.
> Backward compatibility: additive only; no existing channel/provider pair is affected.

**RFC 0004 Amendment N — GitHub PAT Credential Type:**

> Applied per `bu-dlelt`'s implementation. `github_pat` is added to the registered
> `entity_info.info_type` table: Secured=yes, Notes="Fine-grained PAT scoped to the
> `Events` (read) permission only; classic PAT is rejected as insufficiently
> least-privilege." A new companion-entity role, `github_account`, is added alongside
> `steam_account` for the account-registry pattern described in `connector-github`'s D2.

## Risks and trade-offs

- Metadata-tier default for private-repo events (D6) means occupation evidence for a
  private-repo-heavy owner will be coarser (event type + repo name + count, no titles).
  This is deliberate: the alternative — ingesting private commit/PR/issue text by default —
  is a real confidentiality exposure (proprietary code names, security-sensitive commit
  messages) the connector has no business making without a separate, explicit owner
  decision. Richer private-repo detail is a future opt-in, not this contract's default.
- The 300-event/30-day window (D4) means an account connected today can never produce
  occupation evidence older than its connection date through this endpoint. This is an
  honest provider limit, not an implementation gap — no polling strategy closes it.
- id-based cursoring (D5) is slightly more complex than a naive `since` timestamp filter,
  but the Events API has no `since` query parameter at all, and a timestamp-based cursor
  would silently break under the provider's own disclosed out-of-order/latency behavior.
- Extending the poll interval under low rate-limit budget (D7) trades a small freshness
  cost for never exhausting a shared account's hourly quota — acceptable given there is no
  freshness SLA to protect in the first place (D11).

## Delivery gates

1. Land this draft only after independent exact-head semantic review returns GO or
   corrections are applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any
   implementation work claims this contract as authority.
3. Implementation happens under `bu-dlelt` (unmodified by this draft) or an explicitly
   coordinator-approved successor, applying the drafted RFC amendment text above verbatim
   once the corresponding code exists, plus the tests in `tasks.md`.
4. Treat account connection, PAT creation, deployment, and any live GitHub API call as
   separate, later-authorized acts this draft does not perform or authorize.

## Open questions

None are silently decided here. Whether a future Chronicler `github.activity` adapter uses
`projection_path: chronicler_adapter` or a different shape is explicitly left to that
adapter's own separate `chronicler-source-compatibility` delta (D9) — it is not assumed or
scheduled by this change.
