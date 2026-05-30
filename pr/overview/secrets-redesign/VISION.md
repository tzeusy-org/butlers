# Vision — secrets redesign

> Distilled from `README.md` (thesis + five rules) and `BRIEF.md` (Stage 1
> brief) on 2026-05-24 by the `butlers-redesign-prompt` skill. This is the
> binding Section 0 of the redesign brief.

## Problem being solved

Today's `/secrets` is a 3-tab shell (System / User / CLI runtimes) wrapping a
flat `SecretsTable` of `••••••••` rows with a per-row eye-toggle reveal. Two
specific pains:

1. **Opaque without leaking.** To know whether a credential is *the right
   one*, the owner has to reveal it. There is no fingerprint, no last-verified
   timestamp, no scope inventory, no provider-side state — the eye-toggle is
   the only diagnostic, and it is binary.
2. **Flat rhythm.** A silently-expired Google OAuth and a healthy Telegram
   token render at identical weight. Severity has no visual privilege; sick
   credentials hide in plain sight.

Compounding both: the User tab stacks six bespoke provider Setup cards
(Google, Spotify, Home Assistant, WhatsApp, OwnTracks, Steam) in a single
`<Card>`, each with divergent chrome — so the page has no visual rhythm
even on a healthy day.

## Primary audience

**Owner** — single principal, per `about/heart-and-soul/security.md`. The
owner rotates System keys, opens OAuth dances, owns the OAuth callbacks, and
touches every credential family (System, User, CLI runtimes).

The identity switcher in the spine is a **projection lens** over the owner's
view of household-member contact data — *not* an authentication boundary.
Switching identity re-projects the User-tab credentials associated with a
member entity, but every action (rotate, reauthorize, disconnect, probe)
runs with owner privilege; the page does not log a member in. This matches
existing single-owner doctrine (`security.md:8,18-20`) without introducing a
new privilege tier.

A future RFC under `about/legends-and-lore/` may introduce a
household-member privilege tier with its own session-identity mechanism. If
and when it does, this page will gain real member-scoped enforcement; the
current redesign is forward-compatible with that change because the same
`?identity=<id>` URL state will then bind to a session principal rather than
to a projection lens.

External users / operators are explicitly **not** in scope.

## Deliberate design moves

1. **Replace the masked-value blob with *evidence about the value*.** Each
   credential surfaces a stable fingerprint (`sha256:7a3f…`, mono), a scope
   inventory (granted vs required, with mismatches called out), a
   last-verified probe outcome (latency + ok/fail + tail), provider-side
   state (revoked / expiring / valid), and — when sick — an explicit *what
   breaks* list of butler features that will silently fail. Reveal stays as
   a tweak (default `eye`); evidence is the primary affordance.

2. **Passport-book IA.** Replace the 3-tab `<Tabs>` shell with a single
   passport-book surface. Left **spine** indexes every credential
   (pinned `needs-hand` group, then CLI runtimes, then System, then User
   integrations). Right **page** opens the focused credential in editorial
   depth: heading + state plaque, dense KV band, scopes when applicable,
   *what breaks*, probe result, audit stamps, cross-references, commit
   footer.

3. **Severity earns visual authority only when state demands it.** Per
   Dispatch §1b, a quiet day on `/secrets` reads as a calm inventory with
   zero red/amber pixels. The moment one OAuth expires, that one row claims
   colour and weight (sliver + commit pill) — and only that row. Status is
   one of {dot, sliver, numeral, colour}, never a word.

4. **One row template across all three families.** The User tab's six
   bespoke provider Setup cards collapse into one row template; per-provider
   oddities (OwnTracks webhook URL, Steam ID format, WhatsApp QR link) live
   in a provider-specific drawer. System / User / CLI runtimes read as the
   same family in the spine and as the same page-shape on the right.

5. **Inventory ≠ channel-health dashboard.** `/secrets` is the credential
   inventory; `/ingestion/connectors` is the channel-side view of the same
   OAuth (throughput, scope, route). Both pages can trigger reauth and both
   reflect status; OAuth callback returns the user to whichever page
   initiated the dance. Deep-links from `/ingestion/connectors` banners
   land on `/secrets/user#<provider>` at the focused passport page, not a
   tab top.

## What we are deliberately NOT doing

- **No storage migration.** System secrets stay in `butler_secrets`. User
  secrets stay on `entity_info`. CLI runtime tokens stay where they live
  today. The redesign is presentational + adds a small number of new
  read-side endpoints.
- **No bulk operations** (yet). Bulk rotate / bulk revoke / bulk export are
  out of scope. One credential at a time.
- **No merge with `/settings`.** `/settings` is system-side knobs; `/secrets`
  is credentials. Strictly disjoint surfaces.
- **No attempt to be the ingestion-channel health dashboard.** That lives
  at `/ingestion/connectors`. Cross-link, don't duplicate.
- **No padlock icons as row decoration.** The whole page is about secrets;
  drawing a padlock on every row is noise. Use the icon once, in the sidebar.
- **No asterisks as the only proof a secret exists.** `••••••••` is a weak
  signal; pair with fingerprint + last-verified, always.
- **No brand-coloured "Connect" / "Reauthorize" CTAs.** Reauthorize is a
  Dispatch commit pill (fg-on-bg). Provider name appears in mono, never as
  its logo or hex.
- **No status-as-a-word badges.** "Connected" / "Active" / "Linked" are
  banned. State is rendered as {dot, sliver, numeral, colour}.
- **No stacked bespoke provider Setup cards.** Replaced by the single row
  template (move 4).
- **No making the reveal-eye disappear.** It ships and remains the default,
  exposed as a per-page Tweak (`eye / hover / never`). Removing the eye is
  not the goal; demoting it from primary affordance to fallback is.

## Success criteria

- The owner can distinguish a healthy credential from a sick one **without
  pressing the eye on any row**, by reading the spine alone.
- The identity switcher reprojects User-tab credentials per household-member
  entity, with all mutations still running under owner privilege (per the
  projection-lens semantics above).
- An expired Google OAuth surfaces in the `/secrets` spine within the same
  page-load that surfaces it on `/ingestion/connectors` — both views read
  the same source-of-truth state.
- The owner reauthorizes an expired OAuth in one click from either
  `/secrets` or `/ingestion/connectors`, and the OAuth callback returns
  them to the originating page.
- A day on which all credentials are healthy renders `/secrets` with
  **zero red/amber pixels**.
- Deep-links of the form `/secrets/user#<provider>` and
  `/secrets/system#<key>` land on the focused passport page (right-side
  page open, spine row highlighted), not a tab top.
- The reveal-eye is reclassified as a Tweak (default `eye`); removing the
  eye from a row does not impair the owner's ability to assess that row.
- The User tab's six bespoke Setup cards are replaced by one row template
  with provider-specific drawers; visually all three families share rhythm.
- Cross-page reauth bookkeeping survives an OAuth round-trip: the callback
  knows which page sent the user out and lands them back there.
