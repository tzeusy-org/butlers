## Context

`bu-o5lok` is a spec-first prerequisite for `bu-27dxl.15` (perception expansion, move 15). Its
governing evidence packet is
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/bu-27dxl.15-proposal.md`
("Candidate C"), a coordinator-reviewed, binding shaping packet (see
`.../coordinator-review.md` for the corrections that packet survived). This document is the
field-by-field contract a future implementer and reviewer can both check code against, and the
evidence record for why the original "learning intent from anything watched" outcome is being cut
down rather than shipped as specified.

`design.md` per this repo's `specify-*` precedent (`specify-state-first-secrets-passport-index`,
`specify-home-presence-owner-entity-configuration`) is where the judgment calls and their
rationale live; `proposal.md` carries the summary and `specs/.../spec.md` carries the normative
requirement text.

## Evidence Table (live-fetched 2026-09-09)

All URLs fetched directly via `WebFetch` against `developers.google.com` on 2026-09-09; this is
not from model memory.

| Question | Finding | Source |
|---|---|---|
| Does `activities.list(mine=true)` return general watch history? | No. "The documentation explicitly does not return watch history—only channel activity events that match the request criteria." | `https://developers.google.com/youtube/v3/docs/activities/list` |
| What `snippet.type` values does `activities.list` currently document? | `upload`, `comment` (marked "not currently returned"), `playlistItem`, `promotedItem`, `recommendation`, `social`, `channelItem`. **No** `like`, `favorite`, `subscription`, or `bulletinPost` value is documented; `bulletinPost` is explicitly retired ("YouTube has deprecated the channel bulletin feature... `activities.list` does not still return channel bulletins"). | `https://developers.google.com/youtube/v3/docs/activities`, `https://developers.google.com/youtube/v3/docs/activities/list` (fetched independently, twice, consistent both times) |
| `activities.list` quota cost | 1 unit per call | `https://developers.google.com/youtube/v3/docs/activities/list` |
| `youtube.readonly` scope | `https://www.googleapis.com/auth/youtube.readonly` — "View your YouTube account" | `https://developers.google.com/youtube/v3/guides/auth/installed-apps` |
| Can `playlistItems.list` read the owner's own playlists? | Yes, via `playlistId`, for the owner's **regular** playlists. | `https://developers.google.com/youtube/v3/docs/playlistItems/list` |
| Can `playlistItems.list` read Watch History or Watch Later? | No. "Watch history data cannot be retrieved" and "Items in 'watch later' playlists cannot be retrieved through the API." | `https://developers.google.com/youtube/v3/docs/playlistItems/list` |
| `playlistItems.list` quota cost | 1 unit per call | `https://developers.google.com/youtube/v3/docs/playlistItems/list` |
| `playlistItems.list` pagination | `pageToken`/`nextPageToken`, no `updated__gt`-style filter | `https://developers.google.com/youtube/v3/docs/playlistItems/list` |
| Default daily quota budget | 10,000 units/day (combined, excluding `search.list`/`videos.insert` special buckets) | `https://developers.google.com/youtube/v3/determine_quota_cost` |

**Conclusion**: of the two YouTube Data API read surfaces that could plausibly carry a "learning
intent" signal, neither exposes general watch behavior. `activities.list`'s remaining types are
either not owner-authored (`recommendation`, `promotedItem` are YouTube's own suggestions),
irrelevant to consumption (`upload` only fires for channel owners who publish videos, not
consumers), or too generic (`social`, `channelItem`). The one owner-authored, explicit-intent
signal available anywhere in the API is **the owner adding a video to one of their own non-Watch-
Later playlists** — visible via `playlistItems.list(playlistId=<owner's own playlist>)`.

## Goals / Non-Goals

**Goals:**

- Resolve `bu-27dxl.15`'s YouTube outcome to a decision: cut the general signal, specify a narrow
  substitute, and gate its promotion behind explicit owner approval (not unilateral closure).
- Fully specify the narrow substitute (playlist curation) to the same rigor as an implementation-
  ready capability spec, so a future bead can implement directly against it once approved.
- Amend RFC 0018 with the current, precise evidence so this debate does not need to be relitigated
  from stale "marginal" phrasing.

**Non-Goals** (per `bu-o5lok`'s own acceptance criteria and `bu-27dxl.15`'s non-goals):

- No implementation, provider/account/credential/data access, runtime/configuration, message
  delivery, activation, deployment, or merge of the underlying connector.
- No claim that this narrow signal satisfies the *original* "learning intent from watching"
  outcome — it does not, and the spec says so explicitly.
- No dashboard config UI for selecting the designated playlist(s) in this bead.

## Decisions

### Cut general learning-intent; specify playlist-curation as the sole viable substitute

**Decision**: recommend the principled cut. The original outcome ("learning-intent signal for
Education" inferred from what the owner watches) is not achievable — no official, current YouTube
Data API surface exposes watch history, likes, favorites, or subscriptions. Rather than closing
`bu-27dxl.15`'s YouTube slice with nothing, this draft specifies the one concrete, narrower
substitute the live API does support (owner-curated playlist additions) as a candidate for owner
approval.

**Why an owner-gated substitute rather than a flat close**: `bu-27dxl.15`'s own design record says
"any cut or semantic change needs owner approval, not unilateral closure," and `bu-o5lok`'s
acceptance criteria require either "a concrete principled-cut recommendation" or a fully specified
supported signal — a fully specified narrow substitute, explicitly labeled as a narrower semantic
than originally asked for and gated on owner sign-off, satisfies both without silently dropping the
outcome or fabricating an unsupported one.

**Engineering-allocation call (autonomous, per this session's decision-autonomy protocol)**: which
concrete mechanism to specify (`playlistItems.list` on an owner-designated playlist) versus
`activities.list(type=playlistItem)` is decided here, not deferred to the owner. `activities.list`
type=`playlistItem` activity records are a side-effect of the same underlying action but are
undocumented as to whether private/unlisted playlist additions reliably generate a public activity
record, and Google explicitly deprecates reliance on the activity feed for anything beyond broad
discovery use cases in the current docs' framing. Querying `playlistItems.list` directly against a
playlist ID the owner explicitly configures is a direct, well-documented, unambiguous read with a
stable pagination contract, so it is the specified primitive. `activities.list` is not used by this
contract at all.

### Connector, not a module tool

**Decision**: if approved, this ships as a standalone polling connector (`connector-youtube-
learning-signal`), structurally mirroring `connector-google-health` (reuses
`public.google_accounts` + the shared Google credential pipeline; one poll loop per eligible
account; `ingest.v1` envelope emission to the Switchboard).

**Why**: per `connector-base-spec`, background, continuous, cursor-checkpointed ingestion of
discrete external events is exactly the connector pattern already used for every other Google
resource (Calendar, Drive, Health). A module tool is an on-demand, in-daemon capability invoked by
an LLM session; there is no such interactive use case here — the owner does not ask a butler "what
did I add to my learning playlist," they passively accrue Education-lane events over time the same
way Google Health accrues wellness events. This is an engineering-allocation call, not a
product/privacy choice, and is decided here per this session's decision-autonomy protocol.

### Designated playlist, not the whole channel

**Decision**: the connector polls only playlists the owner has explicitly named in per-account
config (`public.google_accounts.metadata.youtube_learning_playlist_ids: string[]`), never the
owner's full playlist list, uploads, or subscriptions.

**Why**: enumerating and polling every playlist the owner has (including private, non-learning
playlists — "gift ideas," "recipes," etc.) would silently widen the signal back toward general
behavior inference, which is exactly the over-claim this bead exists to prevent. Requiring an
explicit, owner-configured allowlist keeps the signal to genuinely deliberate curation. The config
surface for populating that list (a dashboard route, CLI, or manual DB seed) is out of scope for
this bead; the spec only defines the data shape and connector behavior once it is populated.

### Scope set registration is inert until wired

**Decision**: the `youtube` scope set is added to the `google-multi-account-oauth` registry (so a
future OAuth start call *can* request `youtube.readonly`), but no dashboard route, connector
startup path, or default scope composition may request it until a separately-approved
implementation task does so. This mirrors the existing `health` scope set's `force_consent`/opt-in
pattern (Google Health scopes are never included in default scope composition either) and is made
an explicit spec scenario rather than left implicit, so `check_spec_overwrites.py` and a future
implementer cannot mistake "registered" for "authorized to request."

## Risks / Trade-offs

- **The substitute may be judged too narrow to be worth building.** That is an acceptable outcome
  of this draft — Task 3 (owner gate) exists precisely so the owner can decline the narrowed scope
  instead of a worker inferring approval. If declined, `about/legends-and-lore/rfcs/0018-...` retains
  YouTube as P3-deferred with the refreshed evidence, and no implementation bead is ever created.
- **`playlistItems.list` cannot detect *removal* from a playlist**, only pagination through current
  contents; the spec's cursor semantics (§ `connector-youtube-learning-signal` spec) are
  append-detection only (new item IDs since last poll), matching how `connector-google-health`
  treats one-directional record accrual. Playlist reordering or deletion produces no negative
  event; this is stated as an explicit non-goal in the spec, not silently absent.
- **A YouTube account may have no linked channel at all** (a bare Google account never used for
  YouTube) — `channels.list(mine=true)` returns an empty list in that case. The spec's "no channel
  linked" scenario treats this the same way `connector-google-health` treats "no health-scoped
  accounts": degraded status, no envelopes, periodic re-check, not an error.

## Rollback

Nothing to roll back: no code changes, no migrations, no credential state. If archived and later
found to be the wrong cut, this change and its `google-multi-account-oauth` delta can be
superseded by a fresh OpenSpec change; the `youtube` scope-set registry entry is inert prose until
an implementation wires it up.

## Open Questions For Owner (Task 3 gate)

1. Is the narrowed "explicit playlist curation" signal worth building, given it does not deliver
   general watch/like/subscribe-based learning intent?
2. If yes, should the designated-playlist allowlist be owner-configured via a future dashboard
   surface, or is a one-time manual seed (CLI/DB) acceptable for a single-owner deployment?
