# Secrets redesign — Stage 1 brief

> Stage 1 of the flow in `VERTICAL_PROMPT.md`. Read this, mark anything
> wrong, then sign off and we move to Stage 2 (three direction proposals
> on a single design canvas).

---

## 1. What the page does

`/secrets` is the source-of-truth surface for **every credential the
Butlers system can hold**. It is the only place the owner ever pastes a
raw token, opens an OAuth dance, or confirms that a personal account is
still linked. Every other surface that *reads* a credential's health
(an Ingestion connector banner, a butler's "model is in error" badge, a
QA reauth callout) deep-links here.

Three families of secret live behind the same page:

- **System** — ecosystem-wide credentials in `butler_secrets`. Shared
  defaults plus optional per-butler local overrides. *Examples:*
  `BUTLER_TELEGRAM_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`, LLM API
  keys, `owntracks_webhook_token`.
- **User** — identity-bound credentials on the owner's entity record
  (`entity_info` on the owner entity, plus the OAuth bindings the
  *Integrations* stack manages). *Examples:* Google OAuth, Spotify
  refresh token, Home Assistant long-lived token, WhatsApp link,
  OwnTracks config, Steam key. Owner sees every household member's
  user secrets; members see their own.
- **CLI runtimes** — auth tokens used by command-line agents (Claude,
  Codex, Gemini, etc.). Currently a single card.

The page must support: **connect** (OAuth dance), **reauthorize**
(refresh expiring or expired OAuth), **rotate** (swap the value, keep
the slot), **view scopes/permissions**, **test** (1-call probe), and
**audit history** (who-touched-what). It must *not* try to be the
ingestion-channel health dashboard — that lives at
`/ingestion/connectors`. The two pages share data and either can
trigger reauth; `/secrets` is the inventory, `/ingestion/connectors`
is the channel-side view.

## 2. The page today (production, on `main`)

A 3-tab shell — `System / User / CLI runtimes` — wrapped in a
`<Tabs>` with the page title at 3xl-bold (already a Dispatch
violation: display weight must be 500).

**System tab.** A `<Card>` (forbidden — Dispatch has no cards) holds
a target-picker `<Select>` and an "Add secret" button. Below: a
`<Table>` grouped by category (`core / telegram / email / google /
gemini / home_assistant / general`). Each row carries:

```
[key]  [description]  [Status]  [Source]  [Last Updated]  [Value]  [Actions]
```

`Status` and `Source` are both shadcn `<Badge>`s with three colour-flat
states (Local / Inherited from shared / Missing). `Value` is a masked
`••••••••` with a per-row eye toggle that calls `revealSecret`.
`Actions` is edit/trash icons or a "Set value" / "Override" outline
button when the slot is unfilled.

**User tab.** Owner-only `Integrations` section: a single `<Card>`
with six "Setup card" sub-components stacked
(`GoogleOAuthSection`, `GoogleHealthStatusCard`, `SpotifySection`,
`HomeAssistantSection`, `WhatsAppSection`, `OwnTracksSection`,
`SteamSection`). Each component renders its own bespoke chrome —
button styles diverge, status indicators diverge, the visual rhythm
is broken. Below: an `EntityPicker` that lets the owner switch
identity, then the same `SecretsTable` in `mode="user"` rendering
`entity_info` rows.

**CLI runtimes tab.** A single `<CLIAuthCard>` — outside scope of this
audit but in scope of the redesign (it needs to read as the same
family).

**The two faults the user named:**

1. **Opaque without leaking.** The eye-toggle reveal is binary; it
   tells the user nothing about a credential except whether it's
   filled. There's no fingerprint, no "this key starts with `sk-…`",
   no "this token last verified 4 minutes ago", no "this scope set
   matches what the system needs". To know if a key is *right* you
   have to reveal it.
2. **Flat rhythm.** Every row in the table looks identical. A
   silently-expired Google OAuth and a healthy Telegram token render
   with the same weight. Severity has no visual privilege.

## 3. Adjacencies

| Surface | Relationship |
|---|---|
| `/settings` | System-side runtime configuration. **Strictly disjoint** — settings is system-side knobs, secrets are credentials. No merge. |
| `/ingestion/connectors` | The channel-side view of the same OAuth credentials. Shows throughput, scope, route. Reauth banners deep-link to `/secrets/user#<provider>`. |
| `/audit` | Every secret mutation already writes here. The audit history operation lives at `/audit?actor=secrets`; the secret detail surfaces a recent slice (last 5–10 events). |
| `/entities/:ownerId` | The user-tab's data physically lives on `entity_info` on the owner entity. The user tab is *operationally* the owner's secrets, but the storage is entity-scoped. The detail page links here for non-owner identities. |
| Sidebar | Already has a `/secrets` item with the padlock-shackle icon. The redesign keeps the icon and adds the existing `reauth` badge counter (currently surfacing on `/settings`; it belongs here too — or instead). |

## 4. The hardest call

**How transparent can an opaque thing be?**

A secret's whole point is that the value is hidden. But the user
doesn't need the *value*; they need confidence the value is the
right one, granted to the right scopes, last seen working, and not
about to expire. The redesign's central move is to **replace the
value blob with evidence about the value**:

- a stable fingerprint (`sha256:7a3f…`, displayed mono),
- a scope inventory (granted vs required, with mismatches called out),
- a last-verified timestamp + outcome from the most recent test call,
- a provider-side state (revoked / expiring / valid),
- the failure tail if any recent call failed.

The reveal action stays — but it stops being the primary affordance.
A user should be able to tell a healthy secret from a sick one
without ever pressing the eye.

The second-order call: **how to give the page rhythm without leaking
severity colour into a calm day.** Per Dispatch §1b, state colour
appears only when state demands. If all credentials are healthy, the
page has zero red/amber pixels and reads as a quiet inventory. The
moment one OAuth expires, that row's severity claims its own visual
authority — and only that row.

## 5. Decisions already taken (won't re-litigate)

- **Three families stay three families.** System / User / CLI
  runtimes. The redesign may reshape the tab strip into something
  more editorial (sub-routes, a sticky section index, a single
  scrollable spec sheet — Stage 2 picks the metaphor), but the
  underlying three-family split is settled.
- **Mixed visibility.** The owner sees every household member's user
  secrets via the identity picker; members see only themselves. The
  EntityPicker pattern stays; its chrome may change.
- **Reauth lives where the user is.** Both `/secrets` and
  `/ingestion/connectors` can trigger reauth and both reflect status.
  The OAuth callback returns the user to whichever page they came
  from.
- **Storage stays put.** System secrets in `butler_secrets`, user
  secrets in `entity_info`. The redesign does not migrate either.
- **No bulk operations** (yet). One credential at a time. Bulk
  rotate, bulk revoke, bulk export are out of scope.
- **Reveal is a tweak, not a default.** The reveal-mode tweak (eye /
  hover / never) ships with the page; default value is *eye*, but the
  per-secret evidence is the primary affordance.

## 6. Lifecycle states the design must read at a glance

(Bridging what the codebase has today with what the user named.)

| State | Today's label | Redesign's signal |
|---|---|---|
| Healthy | `Local configured` / `Inherited from shared` | calm — no colour, just a green dot at 6px and a recent verify timestamp |
| Expiring soon | (none — invisible today) | amber sliver on the row + amber pill `expires · 4d` |
| Expired / needs reauth | (none — surfaces as integration failure) | red sliver + red commit pill `re-authorize` |
| Scope mismatch | (none) | amber, plus a `scopes` evidence line listing the missing scope |
| Never set / placeholder | `Missing (null)` | dim, no colour; right-aligned `set value →` link |
| Revoked by provider | (none) | red sliver, serif italic gloss `revoked by Google · 14:21` |
| Rotating mid-flight | (none) | dim + animated `composing…` pill, same pattern as the briefing |

## 7. Operations the page must support

- **Connect** — OAuth dance (User tab) or paste-key flow (System
  tab). Different surfaces, same family.
- **Reauthorize** — refresh an expiring/expired OAuth in place.
- **Rotate** — replace a key value, keep the slot, write to audit.
- **View scopes / permissions** — granted vs required, with
  mismatches surfaced.
- **Test** — 1-call probe against the provider, returns latency +
  ok/fail + tail.
- **Audit history** — last 5–10 secret-events surfaced per credential,
  `open /audit →` for the full reel.

(Revoke / disconnect is implied but not in the user's tick-list;
treat as present but not a primary affordance.)

## 8. Variations & tweaks for Stage 2+

- **3 direction proposals** at Stage 2 — different metaphors, all in
  Dispatch.
- Novelty appetite **8/10**: two proposals stay close to home (a
  *ledger* and a *vault*), the third can be more inventive — a
  **passport book** or a **household supply cupboard** are the
  candidates I'll explore.
- **Tweaks to expose at Stage 3:**
  - Reveal mode — eye toggle / hover to peek / never reveal.
  - Default sort — recency / severity / alphabetical.

## 9. Anti-patterns to defend against in this vertical specifically

(Beyond the Dispatch general list.)

- **Padlock icons as decoration.** The whole page is about secrets;
  drawing a padlock on every row is noise. Use the icon once, in the
  sidebar.
- **Asterisks as the only proof a secret exists.** `••••••••` is a
  weak signal. Pair with a fingerprint and a last-verified timestamp,
  always.
- **Big "Connect" / "Reauthorize" CTAs styled with brand colour.**
  Reauthorize is a Dispatch commit button (fg-on-bg pill), nothing
  else. Use the provider's name in mono, never its logo or hex.
- **Stacking provider Setup cards.** The current User tab stacks six
  bespoke setup cards in a single Card. Six bespoke chromes = no
  rhythm. The redesign replaces all six with one row template that
  handles every provider, plus a provider-specific drawer for the
  oddities (OwnTracks webhook URL, Steam ID format, etc.).
- **Status as a word.** "Connected", "Active", "Linked" — banned. The
  state is one of {dot, sliver, numeral, colour}; never the word.

## 10. Open questions worth confirming before Stage 2

- **Audit retention surface here:** show inline last-5 per credential,
  or a single "audit" sub-route that filters the audit log to secret
  events? Default plan is inline last-5 plus `open /audit →`.
- **CLI runtimes weight:** is this a peer tab (System / User / CLI),
  or a smaller third section folded into the System tab? Default
  plan: keep peer for now, treat it as the same family.
- **Provider testing:** is the 1-call probe always safe and free? For
  LLM API keys, a 1-token completion costs essentially nothing; for
  OAuth, a `userinfo` call is free; for Telegram, `getMe` is free.
  Assume all probes are safe and free unless told otherwise.
- **Member-view scoping:** when a member views `/secrets`, do they
  see *their* User tab only, or do they see System (read-only) too?
  Default plan: their User tab only; the System tab is owner-only.

---

**To sign off:** reply with corrections, additions, or a thumbs-up.
On thumbs-up I move to Stage 2 — three direction proposals on a
single design canvas saved at `secrets-redesign/SecretsProposals.html`.
