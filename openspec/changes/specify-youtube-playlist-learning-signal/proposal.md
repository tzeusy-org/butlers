# Specify the supported YouTube learning-intent signal (principled cut)

## Why

`bu-27dxl.15` (perception expansion, move 15) named "YouTube Data API scope-set — learning-intent
signal for Education" as a remaining outcome. `bu-o5lok` is the required spec-first prerequisite:
the coordinator-reviewed shaping packet at
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/bu-27dxl.15-proposal.md`
("Candidate C") found "No matching bead, connector, module, or capability spec exists," that
`activities.list(mine=true)` "exposes authenticated channel/user activities, not a general
watch-history feed," and required this bead to "first define which official API signals
constitute learning intent... If the signal cannot meet the outcome, close as principled cut
rather than shipping a misleading connector."

RFC 0018 (`about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md`) already
deferred YouTube at P3 with "watch-history context is marginal." This draft re-verifies that
finding against the **current** live YouTube Data API v3 documentation (fetched 2026-09-09, see
`design.md` Evidence Table) and confirms it is worse than "marginal": `activities.list` no longer
returns `like`, `favorite`, `subscription`, or `bulletinPost` activity types at all (Google removed
them; only `upload`, `playlistItem`, `recommendation`, `social`, `channelItem`, and `promotedItem`
remain documented, and `comment` is listed but "not currently returned"). `playlistItems.list`
confirms explicitly that "Watch history data cannot be retrieved" and "Items in 'watch later'
playlists cannot be retrieved through the API." There is no official YouTube Data API surface that
exposes what a video the owner watched, liked, or subscribed to was — the original "learning
intent from anything watched" outcome is **not achievable** with this API.

One narrower signal remains genuinely available and evidence-grounded: `playlistItems.list`
against a specific playlist the owner's own account owns (not Watch Later, not watch history) can
list items the owner explicitly added to that playlist. An owner who deliberately curates a
named playlist (e.g. "Learning") as an act of intent is a real, if narrow, learning-intent proxy —
weaker than the original ask, but concrete, quota-cheap (1 unit/call, official
`activities.list`/`playlistItems.list` docs), and free of the watch-history/like/subscribe
over-claim this bead exists to correct.

This proposal is a **principled cut**, not a unilateral closure: it recommends narrowing the
original "learning-intent signal for Education" outcome from general viewing behavior to
explicit playlist curation, and gates that narrowing behind an explicit owner-approval task
(`tasks.md` §3) before any implementation bead may be created, per `bu-27dxl.15`'s instruction
that "any cut or semantic change needs owner approval, not unilateral closure." No implementation,
credential, provider, or runtime work is authorized by this draft.

## What Changes

- Amend RFC 0018's YouTube deferral entry with the current, live-verified API evidence (no
  watch-history, like, favorite, subscription, or bulletinPost signal exists at all) and record
  that a narrower explicit-curation signal is specified here, pending owner approval.
- Add a new capability spec, `connector-youtube-learning-signal`, fully specifying (as target
  behavior for a *future*, separately-authorized implementation bead) a polling connector that
  emits one `ingest.v1` event per new item added to an owner-designated playlist: account/consent
  scope, refresh/revocation delegation to the shared Google credential pipeline, no-channel/
  no-designated-playlist/empty-result semantics, quota behavior, selective per-account disable, and
  the connector-vs-module decision (connector, mirroring `connector-google-health`).
- Add a `youtube` entry to the `google-multi-account-oauth` Scope Set Registry (`youtube.readonly`
  only) as a `## MODIFIED Requirements` delta, reproducing every existing scenario in that
  requirement per this repo's overwrite-guard convention, and add a scenario making explicit that
  registering the scope set does **not** by itself authorize any dashboard route, connector, or
  OAuth flow to request it.
- No frontend, backend, migration, connector code, credential, or provider-call change. `Tests: +0
  ~0 -0`.

## Non-Goals

- No general watch-history, like, favorite, or subscription signal — confirmed unavailable by the
  live API.
- No transcript extraction, no writes/uploads/comments to YouTube, no OAuth authorization-code
  execution, no live provider or account calls of any kind.
- No implementation bead, migration, or connector code from this draft. Task 3 (owner gate) blocks
  all of §4 (future implementation).
- No dashboard UI/config surface for selecting the designated learning playlist(s) — specified as
  a data contract only; the config surface is future, separately-scoped work.
- No change to `bu-dlelt` (GitHub) or the Readwise reading-capture prerequisite; those are
  independent Candidate A/D packets from the same shaping run.

## Impact

- Docs: `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md` (prose
  amendment, not a delta).
- Specs: new `connector-youtube-learning-signal` capability; `## MODIFIED Requirements` delta to
  `google-multi-account-oauth` (Scope Set Registry).
- No unarchived sibling change currently carries a `## MODIFIED Requirements` block for
  `google-multi-account-oauth`'s `Scope Set Registry` requirement or any `connector-youtube-*`
  capability (verified via `rg -l '^### Requirement: Scope Set Registry$' openspec/changes/*/specs/*/spec.md`
  and `rg -l youtube openspec/changes/*/specs -i`, excluding `archive/`). `add-connector-oauth-scope-surface`
  (open, unrelated) references the existing `OAUTH_SCOPE_SETS` registry in prose only and does not
  carry a competing delta for this requirement.
