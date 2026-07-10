## Why

The `connector-filtered-events` spec documents four `filter_reason` formats
(`label_exclude:*`, policy-rule `<scope>:<action>:<rule_type>`,
`validation_error`, `submission_error`) but never the discretion-layer format
`discretion:ignore:<kind>` that connectors actually emit (introduced by
bu-n0336; see `FilteredEventBuffer.reason_discretion_ignore` and
`classify_ignore_kind`). The spec also never resolves a **privacy divergence**:
for the same class of deliberately-dropped content, the WhatsApp user-client
persists an empty raw payload plus a bounded 200-char preview, while the
Telegram user-client (and, on audit, Gmail, Discord, Google Calendar, Google
Health, Spotify, and the Telegram bot) persist the **full** raw provider
payload (`message.to_dict()` / full event JSON). Content the system chose *not*
to process was being retained in full by most connectors.

## What Changes

- **Normative discretion reason format.** Add a `Filter reason for discretion
  IGNORE` scenario to the `Filtered Event Persistence (Batch Flush)`
  requirement: `filter_reason` SHALL be `discretion:ignore:<kind>` with the
  seven canonical `<kind>` values from `classify_ignore_kind`, and a bare
  `discretion:IGNORE` SHALL NOT be used.
- **Normative privacy tier.** Add a `Filtered-Content Privacy Tier` requirement
  adopting the WhatsApp minimal-retention posture as the standard: every
  `filtered`-status row persists a bounded preview (≤200 chars) and MUST NOT
  persist the full raw payload (`full_payload.payload.raw == {}`). `error`-status
  rows are explicitly exempt — a processing failure is not a discretion
  decision, so its payload is retained for diagnosis and replay. Replay of
  filtered rows is specified as best-effort (metadata + preview only).
- **Spec reconciliation.** The `Filtered Events Table` and `Full Payload Shape`
  requirements are updated so their "full payload for replay" language no
  longer contradicts the privacy tier.
- **Telegram user-client brought into compliance (contained).** The three
  `filtered`-status persistence sites in `_process_message` (connector-rule
  block, global-rule skip, discretion IGNORE) now persist `raw={}` instead of
  `message.to_dict()`. The `error`-status site is unchanged. New unit tests
  cover both the redaction and the error-tier exemption.

## Impact

- **Spec**: `connector-filtered-events` — one ADDED requirement, three MODIFIED
  requirements (union-carried).
- **Connector code**: `src/butlers/connectors/telegram_user_client.py` — three
  one-line `raw=` changes in the filtered-status persistence path only.
- **Tests**: `tests/connectors/test_telegram_user_client.py` — three new tests;
  `tests/connectors/live_listener/test_discretion_ignore_persistence.py` — two
  stale cross-reference comments updated (Telegram no longer diverges).
- **Follow-up (reported, not in this PR)**: six other connectors (Gmail,
  Discord, Google Calendar, Google Health, Spotify, Telegram bot) still persist
  full raw for filtered content and must be brought into compliance with the
  now-normative privacy tier. This is a broader change with per-connector test
  surface and is filed as a follow-up rather than bundled here, per the bead's
  containment guidance.

## Non-Goals

- Migrating or scrubbing already-persisted `filtered_events` rows that contain
  full raw payloads. The tier governs new writes; historical rows age out under
  the existing 90-day partition retention.
- Changing `error`-status persistence, replay mechanics, or the replay drain
  path.
