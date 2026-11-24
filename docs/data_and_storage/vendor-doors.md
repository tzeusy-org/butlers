# Vendor doors

A vendor door is a link in an owner notification that opens a vendor page whose host the owner
confirmed. It never carries an amount, cart contents or confirmation token, so following it spends
nothing. The design is `openspec/changes/specify-vendor-doors`; this page records what core_336
(bu-jv1moa.40, PR1) installs. It is dark: `butlers.core.vendor_doors.VENDOR_DOORS_LIVE` is `False`
and no sender, receiver or consumer resolves a door yet.

## Registry

`public.vendor_doors` holds confirmed doors and the inert proposals around them. Kinds are exactly
`cancel`, `manage`, `return` and `check_in`. A template is `https://host/path` only: no query, no
fragment, no userinfo, no port, slots (`{order_ref}`, `{booking_ref}`, `{account_ref}`) only inside
path segments, at most 300 characters after substitution.

No role holds INSERT, UPDATE, DELETE or TRUNCATE on the table, and a trigger refuses DELETE and
TRUNCATE outright. Every write is a SECURITY DEFINER function, each with a standalone
`REVOKE ALL ... FROM PUBLIC` and one role set granted EXECUTE:

| Function | Caller (checked with `current_setting('role', true)`) |
| --- | --- |
| `vendor_door_propose` | `butler_switchboard_rw` gives origin `email`, `butler_finance_rw` gives `extracted`, any other role 42501 |
| `vendor_door_create`, `_confirm`, `_revoke`, `_reject` | the NOLOGIN capability role `dashboard_vendor_doors` (created by `scripts/init-db.sql`; membership is host provisioning) |

`vendor_door_propose` takes no origin, verdict or sender argument. For email it reads the sender
address and the stored `sender_authentication` verdict from `public.ingestion_events` by event id
and fails closed to unverified. A proposal is verified-tier only when the verdict is `authenticated`
and the sender's registrable domain equals the link host's. Pending proposals are capped per tier
bucket (verified: 5 per host and kind, 5 per sender domain; unverified email and extracted: 5 per
host and kind, 50 per owner each), expire after 30 days, and `dashboard` and `legacy_owner_set`
origins are exempt. Confirm, revoke and reject are version compare-and-set.

`public.vendor_doors_current` exposes confirmed rows of the live owner to Finance, Switchboard and
Messenger. Proposals are readable only by the dashboard capability role.

## Registrable domain

`public.registrable_domain(text)` and `butlers.core.registrable_domain` compute the eTLD+1 under the
Public Suffix List (ICANN and private sections) from the vendored snapshot
`src/butlers/data/public_suffix_list.dat`, loaded into `public.public_suffix_rules` by core_336. A
parity test pins the SQL function, the table and the Python function together. Refresh the list
only in a reviewed PR that updates the file and adds a migration reloading the table. Anything that
cannot be computed returns NULL and counts as unverified. The two-label helper in
`roster/finance/tools/cancellation_door.py` is display-only.

## Dashboard routes

`/api/vendor-doors` (list, create, confirm, revoke, reject; `src/butlers/api/routers/vendor_doors.py`)
sits behind `require_dashboard_owner_control` and answers 404 while `VENDOR_DOORS_LIVE` is `False`.
It uses its own pool and login (`DASHBOARD_VENDOR_DOORS_DB_USER` / `_PASSWORD`), never the
dashboard-auth login: `src/butlers/api/vendor_door_store.py` refuses at connect time any login that
is not a member of `dashboard_vendor_doors` or that reaches any other role, superuser, createrole,
createdb, bypassrls, replication or the database owner. Each call runs `SET LOCAL ROLE
dashboard_vendor_doors` and then one definer function; there are no direct table writes. Granting
the login membership is host provisioning, like the capability role itself.

No request body accepts an ingestion event, origin, tier, verdict or lineage (extra fields are a
422), so a route can only create an owner-typed `dashboard` row and can never attach a template to
someone else's event. Binding a template to the event it was read from belongs to the proposers
(Switchboard and Finance), whose origin the database derives from the calling role.

## Notify contract

A notification references a door only through `OpenDoorActionV1 {verb: "open_door", reference:
DoorReferenceV1}`: valid on `send` and `insight` intents, alone and at most once, never on
`approval_request`, `reply` or `react`, and refused under `render_links_inert`. A producer can
never submit the delivered `open_link` form (`server_open_link_only`). The delivered door output
has provenance `vendor_door` and is rebuilt by the receiver from the reference alone.

### Resolution and degradation

The Switchboard sender and the Messenger receiver each resolve the door independently
(`resolve_request_links`), on a connection of their own from the pool and outside any transaction:
the resolver's deadline cancels an in-flight query, which would abort an enclosing transaction.
Only Telegram renders the link, as one server-built URL button; the message text carries no URL.

When the door cannot be verified (stale version, revoked or unconfirmed, flag off, read failure or
timeout, no pool) the action is replaced by an `open_dashboard` button to the fixed Finance
subscriptions page (`finance_subscriptions`, `/butlers/finance?tab=finances`) and the message gets
` [link not verified]`. Under exposure (`render_links_inert`, or the sender's inert-content mark) or
on a channel without buttons, or if that page cannot be resolved either, it is plain text plus the
marker and no button. The fallback is the only non-door action a `send` or `insight` may carry, and
only the server mints it. The notification log keeps the door reference, never its URL.

## Rollback

core_336 downgrade refuses while any door row exists. With the flag off nothing resolves a door.
