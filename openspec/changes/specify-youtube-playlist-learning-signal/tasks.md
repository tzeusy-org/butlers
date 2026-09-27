## 1. Spec artifact (this change)

- [x] 1.1 Live-fetch current YouTube Data API v3 documentation (`activities.list`,
      `playlistItems.list`, OAuth scopes, quota costs) and record the Evidence Table in
      `design.md`; confirm no general watch-history, like, favorite, or subscription signal exists.
- [x] 1.2 Decide and record the principled cut: narrow the original outcome to explicit
      playlist-curation only, rather than closing the outcome or fabricating a broader signal.
- [x] 1.3 Fully specify `connector-youtube-learning-signal` (account/playlist eligibility, OAuth
      lifecycle, cursor/poll/quota semantics, envelope content) to implementation-ready rigor, per
      `bu-o5lok` acceptance criterion 1 (bounded outcome, non-goals, trust boundaries, failures,
      idempotence, compatibility, rollback).
- [x] 1.4 Add the `youtube` entry to `google-multi-account-oauth`'s `Scope Set Registry`
      requirement as a `## MODIFIED Requirements` block reproducing every existing scenario
      verbatim, per this repo's overwrite-guard convention (`AGENTS.md` § "Two unarchived OpenSpec
      changes can silently overwrite each other").
- [x] 1.5 Amend RFC 0018's YouTube deferral entry with the refreshed evidence and a reference to
      this change.
- [x] 1.6 Confirm no other unarchived change carries a `## MODIFIED Requirements` block for
      `google-multi-account-oauth`'s `Scope Set Registry` requirement or any `connector-youtube-*`
      capability (`rg -l '^### Requirement: Scope Set Registry$' openspec/changes/*/specs/*/spec.md`;
      `rg -li youtube openspec/changes --glob '*/specs/**'`, excluding `archive/`).

## 2. Validation (this change)

- [ ] 2.1 `openspec validate specify-youtube-playlist-learning-signal --strict`.
- [ ] 2.2 `python3 scripts/check_spec_overwrites.py` — no unfrozen baseline losses.
- [ ] 2.3 `python3 scripts/check_countable_tasks.py`.
- [ ] 2.4 `make check-guards`.
- [ ] 2.5 Independent exact-head semantic review returns GO or corrections (`bu-o5lok` acceptance
      criterion 5).

## 3. Owner gate (blocks all future work)

- [ ] 3.1 Obtain exact owner approval of the principled cut: is the narrowed explicit-
      playlist-curation signal worth building, given it does not deliver general watch/like/
      subscribe-based learning intent (`design.md` "Open Questions For Owner")? Any semantic
      change to this cut requires fresh review and approval.
- [ ] 3.2 If declined, leave RFC 0018's YouTube entry at P3-deferred with the refreshed evidence
      and do not create an implementation bead. If approved, record the approval and the answer to
      `design.md` open question 2 (owner-configured dashboard surface vs. one-time manual seed for
      `youtube_learning_playlist_ids`).

## 4. Future implementation after approval (out of this bead)

- [ ] 4.1 Register the `youtube` scope set's actual OAuth wiring: a dashboard route or config path
      that can request `scope_set=youtube` for a chosen account, per the approved answer to open
      question 2.
- [ ] 4.2 Add `public.google_accounts.metadata.youtube_learning_playlist_ids` (additive JSONB
      field write path; no migration if `metadata` already exists per
      `google-multi-account-oauth`'s `Additive Schema Support` requirement).
- [ ] 4.3 Implement `connector-youtube-learning-signal` per this change's spec: per-account/
      per-playlist polling loops, shared Google credential pipeline delegation, cursor
      checkpointing, `ingest.v1` envelope emission, heartbeat/metrics.
- [ ] 4.4 Assert the owner `has-email` entity-facts triple on first `youtube.readonly` grant,
      mirroring `connector-google-health`'s pairing flow.
- [ ] 4.5 Real-Postgres and API tests for every scenario in the capability spec: baseline pass
      (no envelopes), steady-state addition detection, empty playlist, deleted/inaccessible
      playlist, no-channel-linked account, 401/revocation, quota exhaustion, multi-account
      isolation.
- [ ] 4.6 File a new bead for this implementation step after owner approval; do not fold it into
      `bu-o5lok` or `bu-27dxl.15`.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync
      `connector-youtube-learning-signal` and the `google-multi-account-oauth` delta to
      `openspec/specs/`, then archive this change. Archival does not authorize deployment.
