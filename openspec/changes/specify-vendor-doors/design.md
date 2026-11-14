## Context

Grounded in main at 53832dfda9. Seams not verifiable from code are marked `[Unknown]` with a task.

Found on main:

- S1 references: `roster/switchboard/tools/notification/self_links.py` resolves `SelfLinkReferenceV1`
  against the fixed `SELF_ROUTES`; the sender (`resolve_self_links`) and the Messenger receiver
  (`parse_delivered_notify_request`) each resolve independently, discard wire label/url/host, and
  fall back to `open_dashboard` plus `[link not verified]`. URL rules there (https, no userinfo,
  no IP, no `xn--`, no non-default port, at most 300 characters) are the base for door URLs.
- Subscription links: `finance.subscriptions.cancellation_url` with `cancellation_url_source` and
  `status_source` (`owner` or `extracted`, finance_017, NOT NULL default `extracted`). Writes go
  through `track_subscription` (`roster/finance/tools/subscriptions.py`), which derives the source
  from the server-held routing context (`runtime_signal_source`), never from arguments, and never
  lets an extracted write overwrite an owner value.
- Renewal consumer: `insight-scan` section 4 in `roster/finance/jobs/finance_jobs.py` warns for
  `status='active'` or for `cancelled/paused` with `status_source='extracted'`, and prints
  `cancellation_url` only when `cancellation_url_source='owner'`
  (`door_verified`). Tests: `test_insight_scan_renewal_with_known_door_warns_with_days_remaining`
  and `test_insight_scan_owner_set_cancelled_suppresses_the_renewal_warning`
  (`roster/finance/tests/test_jobs.py`).
- Sender verdict: `public.ingestion_events.sender_authentication` (`authenticated`, `unverified`,
  `failed`, or NULL for non-email), written once at ingest by `sender_authentication_verdict`
  (core_271, bu-s11n0s.20).
- Owner-only API: `require_dashboard_owner_control` (`src/butlers/api/owner_control.py`) accepts
  only a request carrying the central verified `owner_authority`; scoped connector callbacks never
  satisfy it. Routers live in `src/butlers/api/routers/`.
- Shared tables: new `public` tables come from a core alembic revision and need REVOKE-by-default
  grants, a row in `src/butlers/db_authority/grants.py`, and schema-reach and grants-manifest
  entries (see core_331 and `owner_delivery_subject_members`).

## D1: Owners

- Schema and migration owner: the core alembic chain (`alembic/versions/core/`), table
  `public.vendor_doors`. A finance-owned table was rejected because Switchboard and Messenger must
  resolve doors from other schemas, and `public` is the only cross-butler namespace.
- Writer: only the dashboard API role, through SQL functions (`public.vendor_door_confirm`, `public.vendor_door_reject`,
  `public.vendor_door_revoke`) that enforce the URL rules, the CAS, and the owner binding in the
  database, so a bug in the router cannot store an unsafe row. Direct INSERT/UPDATE/DELETE is
  revoked from every role including the dashboard role.
- Function grants: each of `vendor_door_confirm`, `vendor_door_revoke`, `vendor_door_reject` and
  `vendor_door_propose` gets its own standalone `REVOKE ALL ON FUNCTION ... FROM PUBLIC` (outside
  any tolerant grant block), then `GRANT EXECUTE` to exactly one role set: the dashboard role for
  confirm, revoke, reject and create; Finance and Switchboard for propose. The table, the view and the five
  functions each get a row in `src/butlers/db_authority/grants.py` and the schema-reach and
  grants-manifest inventories (the catalog test is Docker-only, so the real-PG test in task 2.1
  asserts PUBLIC and every other role are refused EXECUTE).
- Proposal writer: Finance (the extracted-link path) and Switchboard (email-derived proposals)
  insert through `public.vendor_door_propose`, which can only create `status='proposed'` rows and
  can never touch a confirmed row. Origin and verdict are bound to the caller, never arguments:
  - `vendor_door_propose` derives `origin` from `session_user` (Switchboard gives `email`, Finance
    gives `extracted`) and takes no origin argument.
  - For `email`, it reads the verdict and the sender address from `public.ingestion_events` by the
    `ingestion_event_id` argument, never from arguments, and fails closed (unverified) if the row
    or verdict is missing.
  - `legacy_owner_set` is written only by the finance migration (D5), under the migration role.
  - `dashboard` origin is created only by a new `public.vendor_door_create` function (EXECUTE only
    for the dashboard role, standalone `REVOKE ALL ... FROM PUBLIC`, its own `grants.py` row) behind
    `POST /api/vendor-doors` and `require_dashboard_owner_control`. This is the owner's fallback
    when a legitimate link is starved out by the caps (D2b). [Unknown] exact dashboard and finance role names: task 1.1
  reads `db_authority/grants.py` and picks the existing roles rather than creating new ones.
- Readers: Finance, Switchboard, Messenger and the dashboard get SELECT on the resolver view
  `public.vendor_doors_current` (confirmed, unrevoked rows only). Proposals are readable only by
  the dashboard role.
- API owner: `src/butlers/api/routers/vendor_doors.py`, every route behind
  `require_dashboard_owner_control`. No MCP tool exists for confirmation (non-goal).

## D2: Door record

One row per door, versioned. Columns: `id` UUID, `version` int (CAS), `owner_entity_id`, `kind`
(`cancel`, `manage`, `return`, `check_in`), `vendor_entity_id` and `host` (registrable domain,
lowercase ASCII), `template` (HTTPS scheme, host and path only; typed slots `{slot}` only inside path segments), `slot_types` (JSON map
slot name to one of a closed regex set), `status` (`proposed`, `confirmed`, `revoked`, `rejected`), `expires_at` (proposals only),
`origin` (`dashboard`, `legacy_owner_set`, `extracted`, `email`), `lineage` (JSON: subscription id
and column, or `ingestion_event_id` and the stored `sender_authentication` value copied at proposal
time), `confirmed_at`, `confirmed_by_session` (hash only), `revoked_at`, `created_at`.

- Why a template at all: a door may need a vendor account or order slot (for example
  `https://example.com/orders/{order_ref}/return`). Slots take typed regex values from
  `{order_ref, booking_ref, account_ref}` with `[A-Za-z0-9_-]{1,64}`. A slot value may never
  contain `/ ? # % @ :`, and there is no slot type for amount, cart or token.
- Template shape (structural, checked in the confirm function and again at every resolution): a
  template is scheme, host and path only. HTTPS; no userinfo; host ASCII only, no IP literal, no
  `xn--` label, no non-default port; **no query and no fragment**; slots only inside path
  segments, never in the host. With no query, no parameter can carry an amount, a cart, a confirmation
  token or a redirect target, so the rules need no keyword list; the owner reviewing the full
  template in the confirm view is the guard for what remains in the path. At most 300 characters after slot
  substitution; the host equals the stored `host`. A path that embeds a second URL (a segment
  containing `://` or an encoded form, already excluded because slot and path text may not contain
  `%`) is refused.
- Why not store a bare URL: the pending finance_017 model stores a bare URL, which cannot
  express "confirmed host" separately from the path an extracted email suggests.

## D2b: Bounded lifecycle

- Caps apply only to untrusted pending proposals (`status='proposed' AND expires_at > now()`),
  counted in separate buckets so a flood of one class can never displace another:
  - Verified-tier email: an `email` proposal is verified-tier only when its stored verdict is
    `authenticated` AND the link host's registrable domain equals the sender's registrable domain.
    `authenticated` proves only that the sender's own domain signed the message
    (`account_security.py:350-362`), so `attacker.com` can sign a mail that links `netflix.com`;
    that mail fails the equality and is counted as unverified. Cap: 5 per (owner, host, kind) and
    5 per (owner, sender registrable domain), replacing a shared per-owner cap for this tier.
  - Unverified email (everything else from email, including an authenticated sender whose domain
    differs from the link host; a legitimate vendor using a third-party mailer lands here and is
    still proposed): 5 per (owner, host, kind) and 50 per owner, its own bucket.
  - `extracted` origin: its own bucket, 5 per (owner, host, kind) and 50 per owner, not shared with
    email.
  - `dashboard` and `legacy_owner_set` origins, including every row the D5 backfill writes, are
    exempt from the caps entirely.
  A proposal beyond its own bucket's cap is dropped, not queued. Why: a hostile mail flood, even
  one the attacker DKIM-signs, exhausts only its own bucket or sender-domain slots and cannot
  starve extracted proposals, the migration or another sender domain. One subscription renews
  once a year, so five candidates per vendor and kind is generous.
- A proposal expires 30 days after creation (`expires_at`, NOT NULL exactly while pending: the
  table has `CHECK ((status = 'proposed') = (expires_at IS NOT NULL))`, so confirm, revoke and
  reject null it); an expired proposal is not shown and
  cannot be confirmed. Why: 30 days spans several dashboard visits, and a later renewal email
  re-proposes a fresh row, so an unreviewed link never lingers as a standing candidate.
- Confirmed doors do not expire; the owner revokes them. Revoked, rejected and expired rows are
  retained for lineage and are not counted against the caps once not pending.
- The owner reject path (`POST /api/vendor-doors/{id}/reject`, same CAS) moves a proposal to
  `rejected`. Rejections are retained indefinitely, and `vendor_door_propose` refuses a proposal
  whose (owner, kind, template) equals a rejected row's exact stored template after host
  lowercasing, except that a `dashboard`-origin proposal bypasses this suppression, so the owner
  can always create the real template even after rejecting an attacker's identical one. `slot_types` does not count: the same template with different slot types is the
  same proposal.

## D2c: Registrable domain

"Registrable domain" is an authorization input (verified-tier equality, D2b), so it is the eTLD+1
under the Public Suffix List, ICANN and private sections both. One shared function computes it for
both the sender address and the link host; the sender and the host are never compared with two
different helpers.

- Implementation choice: a pinned `publicsuffix2`-style library with the Public Suffix List
  snapshot vendored in the repo and refreshed only by a reviewed PR. Why: a vendored list makes the
  result deterministic across the database function, tests and CI, and avoids a network fetch or a
  floating list on a security decision. [Unknown] library name and pin: task 1.4 picks one that is
  already a dependency if any.
- The database enforces the same rule: the host stored on the row is the function's output at
  proposal and confirmation time, and the resolver re-derives it from the template.
- If it cannot be computed (no public suffix match, an IP, an empty or malformed address), the
  proposal is unverified. It fails closed.
- Private-section suffixes count: `myshopify.com` is a public suffix, so `a.myshopify.com` and
  `b.myshopify.com` are different registrable domains. `attacker.com.my` and `vendor.com.my` are
  different too: `com.my` is a suffix, never a registrable domain.
- The finance display helper in `cancellation_door.py` (a two-label heuristic) stays display-only
  and is never used for any decision.
- Accepted residual: a legitimate vendor using a third-party mailer is counted unverified, and a
  flood in that bucket can starve it for up to 30 days. The owner's fallback is the dashboard
  create path.

## D3: Confirmation path

Owner-only, dashboard-only:

1. `POST /api/vendor-doors/{id}/confirm` with `{expected_version}` behind
   `require_dashboard_owner_control`. The confirming owner is the authenticated owner authority,
   never a body field; a forged `owner_entity_id` in the body is ignored and a mismatching
   server-held owner is refused.
2. The function does `UPDATE ... WHERE id=$1 AND version=$2 AND status='proposed' AND expires_at > now()` and bumps
   `version`. A stale version or non-proposed status returns the current state with no write
   (idempotent: confirming an already-confirmed identical door returns 200 with the same version).
3. The confirm view (what the owner decides on) shows, for that exact version: the registrable
   host, the full template, the origin (`dashboard`, `legacy_owner_set`, `extracted`, `email`),
   the stored sender verdict, the sender's registrable domain beside that verdict, and whether it
   matches the link host. A vendor display name never appears alone: a look-alike host such as
   `netf1ix.com` is shown as its real registrable domain with its origin, so the owner can see it
   is not the vendor. The version in the view is the version the CAS compares.
4. Revocation is the same shape (`status='revoked'`, version bump); revoking a revoked door is a
   no-op.
5. Refused, with a content-free reason and no row change: a non-owner session, a connector
   callback credential, an MCP or goal caller, a request produced under exposure (the router
   rejects any request that carries goal lineage), and an email- or message-originated request.
   An authenticated email sender is evidence only; it can raise a proposal's displayed trust label
   but never the status.
- Sender verdict: a proposal created from email copies the stored
  `ingestion_events.sender_authentication` value at proposal time into `lineage`. No code path
  reads raw headers. A NULL or unreadable verdict is stored as `unverified`.
- Why dashboard and not Telegram: Telegram `od1` cards bind to one owner-delivery subject and a
  single use; confirming a vendor host is a standing change, so it needs the authenticated
  dashboard session, matching approvals.

## D4: Typed references and resolution

- New reference kind alongside `SelfLinkReferenceV1`: `DoorReferenceV1 {door_id, version, slots}`
  with verb `open_link`. A producer supplies only this; label, url, host and provenance remain
  derived and discarded on the wire as in S1.
- Sender and receiver each resolve independently against `public.vendor_doors_current`. A
  reference resolves only when the row is `confirmed`, belongs to the same owner, the reference
  `version` equals the current row version, every slot is present, typed and rule-clean, and the
  rebuilt URL passes the D2 rules. Anything else (missing, revoked, stale version, foreign owner,
  unconfirmed, read failure, timeout) degrades to `open_dashboard` plus `[link not verified]`.
- Edit race: a confirmed door is immutable. An edit creates a new proposal; confirming it
  supersedes the old row. A reference minted against the old version therefore never resolves to
  the new template.
- The delivered label shows the registrable domain and `provenance: "vendor_door"`, with link
  previews off. Resolution is a read: no broker call, no effect, no cancellation evidence.
- Exposure: a message produced from an exposed invocation never gets a live door. The sender
  refuses to mint one (inert text plus the S1 degradation) because bu-kr3x6j excludes the D4
  carve-out. The receiver repeats the check from delivered lineage.

## D5: Migration of subscription links

- Finance revision `finance_021` (after core creates the table; number fixed last) backfills
  `public.vendor_doors` from `finance.subscriptions`:
  - `cancellation_url_source='owner'` and the URL passes the D2 rules: a `confirmed` row with
    `origin='legacy_owner_set'`, kind `cancel`, lineage `{subscription_id, column}`. This is
    allowed because finance_017 recorded `owner` only for an owner-attested session (server-held
    routing context), never for a routed email.
  - `owner` but the URL fails a D2 rule: a `proposed` row, `origin='legacy_owner_set'`, with the
    failing rule recorded. The subscription column is untouched.
  - `extracted` or unknown: a `proposed` row, `origin='extracted'`, never auto-confirmed.
  - finance_017 backfilled every pre-017 row as `extracted`, so no historical row is inferred to be
    owner. Owner provenance is never reconstructed from old text or metadata.
- The migration is idempotent (unique key on subscription id, column, kind) and keeps
  `cancellation_url`, its source columns and `status_source` unchanged; it deletes nothing.
- Order: finance can only run after the core revision. If `public.vendor_doors` is absent the
  finance revision is a no-op and the renewal consumer falls back to today's column behavior.

## D6: Renewal consumer

- Without `VENDOR_DOORS_LIVE`, `insight-scan` is byte-for-byte today's behavior.
- With it, the verified-door text comes from a confirmed `cancel` door for that subscription,
  rendered through the D4 reference. A proposal never prints as a verified link (it prints the
  existing "not verified" text) and never makes the warning disappear.
- Cancelled status: `status='cancelled'` suppresses the renewal warning only when
  `status_source='owner'`. An extracted or email-reported "cancelled" keeps warning and asks the
  owner to confirm, as today. This change does not touch that rule; doors add nothing to it, and a
  click on a door sets no status.
- Atomic never-overwrite-owner behavior of `track_subscription` stays; an extracted write may
  create or refresh only a proposal, never alter a confirmed door.

## D7: Safety properties

- Bounded cancellation: every resolution read has a deadline; expiry or cancellation yields the
  inert form and never partial output.
- Replay: delivering the same reference twice resolves twice with the same output or the same
  inert fallback; there is no one-shot state.
- No second raw-URL path: no field of the notify contract accepts a URL, host or template.

## D8: Dark rollout and rollback

- `VENDOR_DOORS_LIVE = False` in a new flags module follows the perception flag pattern; no
  activation bead is included.
- Rollback sets the flag false: references resolve inert, confirmation routes return 404, records
  and provenance remain, pending proposals are never promoted, and the finance column behavior is
  the fallback. Down-migration drops nothing that the finance column holds.

## Owner questions

None required. The decisions above were taken as engineering choices inside bu-kr3x6j's closed
scope. Recorded for visibility, not as gates:

- Legacy `owner`-sourced links migrate as confirmed (D5). The alternative (force re-confirmation
  on the dashboard) is safer but turns every migrated link into a proposal; revisit only if the
  owner prefers it, since flipping it is a one-line change to the migration's mapping.

## Migration numbering

core and finance numbers are assigned at implementation time. core_334 and core_335 are in
flight on other branches; task 7.1 takes the next free core number and `finance_021` (or the next
free) at that point.
